"""Paper-based SAP adaptations; forward accepts inputs only, never labels.

References and deviations: docs/wp5-models.md. Time axes are batch-first.
"""

import math

import torch
from torch import nn


INPUT_FIELDS = {"encoder_observed", "encoder_observed_mask", "encoder_known",
                "decoder_known", "decoder_lags", "decoder_lag_mask", "static_categorical"}


def mask_values(values, mask):
    # where, not multiplication: unavailable sentinel values cannot affect output.
    return torch.where(mask, values, torch.zeros_like(values))


class StaticEmbedding(nn.Module):
    def __init__(self, cardinalities, dimension):
        super().__init__()
        # ID 0 is a real unknown category, not a padding index.
        self.tables = nn.ModuleList(nn.Embedding(size, dimension) for size in cardinalities)

    def forward(self, categories):
        return [table(categories[:, i]) for i, table in enumerate(self.tables)]


class Position(nn.Module):
    def __init__(self, dimension, base):
        super().__init__()
        self.dimension, self.base = dimension, base

    def forward(self, x, offset=0):
        positions = torch.arange(offset, offset + x.size(1), device=x.device, dtype=x.dtype)[:, None]
        frequencies = torch.exp(torch.arange(0, self.dimension, 2, device=x.device, dtype=x.dtype)
                                * (-math.log(self.base) / self.dimension))
        pe = torch.zeros(x.size(1), self.dimension, device=x.device, dtype=x.dtype)
        pe[:, 0::2], pe[:, 1::2] = torch.sin(positions * frequencies), torch.cos(positions * frequencies)
        return x + pe


class TransformerForecast(nn.Module):
    """Standard continuous Transformer core for MOEBS hyperparameter search."""
    def __init__(self, contract, config, *, nspace=False):
        super().__init__()
        self.nspace = nspace
        d, e = config["d_model"], config["embedding_dim"]
        self.static = StaticEmbedding(contract["cardinalities"], e)
        self.position = Position(d, config["position_base"])
        obs, known, lags = (len(contract[key]) for key in ("encoder_observed", "encoder_known", "decoder_lags"))
        static_size = len(contract["cardinalities"]) * e
        if nspace:
            # Shared X @ A (L'Heureux 2022, eq. 5); target path is historical
            # in encoder and last observed / previous prediction in decoder.
            self.nspace_projection = nn.Linear(obs * 2 + known + static_size, d, bias=False)
        else:
            self.encoder_projection = nn.Linear(obs * 2 + known + static_size, d)
            self.decoder_projection = nn.Linear(lags * 2 + known + static_size, d)
        enc = nn.TransformerEncoderLayer(d, config["n_heads"], config["d_ff"], config["dropout"], batch_first=True)
        dec = nn.TransformerDecoderLayer(d, config["n_heads"], config["d_ff"], config["dropout"], batch_first=True)
        self.encoder = nn.TransformerEncoder(enc, config["encoder_layers"], enable_nested_tensor=False)
        self.decoder = nn.TransformerDecoder(dec, config["decoder_layers"])
        self.output = nn.Linear(d, 1)
        self.dimension = d

    def forward(self, inputs):
        observed_mask = inputs["encoder_observed_mask"]
        observed = mask_values(inputs["encoder_observed"], observed_mask)
        lag_mask = inputs["decoder_lag_mask"]
        lags = mask_values(inputs["decoder_lags"], lag_mask)
        static = torch.cat(self.static(inputs["static_categorical"]), -1)
        context = lambda known: torch.cat([known, static[:, None].expand(-1, known.size(1), -1)], -1)
        enc_features = torch.cat([observed, observed_mask.float(), context(inputs["encoder_known"])], -1)
        projection = self.nspace_projection if self.nspace else self.encoder_projection
        memory = self.encoder(self.position(projection(enc_features) * math.sqrt(self.dimension)))
        horizon = inputs["decoder_known"].size(1)
        if not self.nspace:
            features = torch.cat([lags, lag_mask.float(), context(inputs["decoder_known"])], -1)
            queries = self.position(self.decoder_projection(features) * math.sqrt(self.dimension), observed.size(1))
            causal = torch.triu(torch.ones(horizon, horizon, device=queries.device, dtype=torch.bool), 1)
            return self.output(self.decoder(queries, memory, tgt_mask=causal)).squeeze(-1)
        previous = observed[:, -1:, :1]
        previous_mask = observed_mask[:, -1:, :1]
        tokens, predictions = [], []
        for step in range(horizon):
            features = torch.cat([previous, lags[:, step:step + 1], previous_mask.float(),
                                  lag_mask[:, step:step + 1].float(),
                                  context(inputs["decoder_known"][:, step:step + 1])], -1)
            tokens.append(self.nspace_projection(features) * math.sqrt(self.dimension))
            queries = self.position(torch.cat(tokens, 1), observed.size(1))
            causal = torch.triu(torch.ones(step + 1, step + 1, device=queries.device, dtype=torch.bool), 1)
            previous = self.output(self.decoder(queries, memory, tgt_mask=causal)[:, -1:])
            previous_mask = torch.ones_like(previous, dtype=torch.bool)
            predictions.append(previous.squeeze(-1))
        return torch.cat(predictions, 1)


class GateAddNorm(nn.Module):
    def __init__(self, d, dropout):
        super().__init__()
        self.gate = nn.Sequential(nn.Dropout(dropout), nn.Linear(d, 2 * d), nn.GLU(-1))
        self.norm = nn.LayerNorm(d)

    def forward(self, x, residual):
        return self.norm(residual + self.gate(x))


class GRN(nn.Module):
    def __init__(self, in_size, hidden, out_size, dropout, context_size=None):
        super().__init__()
        self.first, self.second = nn.Linear(in_size, hidden), nn.Linear(hidden, out_size)
        self.context = nn.Linear(context_size, hidden, bias=False) if context_size else None
        self.skip = nn.Identity() if in_size == out_size else nn.Linear(in_size, out_size)
        self.gated = GateAddNorm(out_size, dropout)

    def forward(self, x, context=None):
        hidden = self.first(x)
        if self.context is not None:
            if context is None:
                raise ValueError("Missing static TFT context")
            while context.ndim < hidden.ndim:
                context = context.unsqueeze(1)
            hidden = hidden + self.context(context)
        return self.gated(self.second(torch.nn.functional.elu(hidden)), self.skip(x))


class VariableSelection(nn.Module):
    def __init__(self, input_sizes, d, dropout, context=False):
        super().__init__()
        self.transforms = nn.ModuleList(GRN(size, d, d, dropout) for size in input_sizes)
        self.weights = GRN(sum(input_sizes), d, len(input_sizes), dropout, d if context else None)

    def forward(self, values, context=None):
        weights = self.weights(torch.cat(values, -1), context).softmax(-1)
        transformed = torch.stack([layer(value) for layer, value in zip(self.transforms, values)], -2)
        return (transformed * weights.unsqueeze(-1)).sum(-2), weights


class InterpretableAttention(nn.Module):
    """TFT eqs. 13-16: head-specific Q/K, shared V, mean across heads."""
    def __init__(self, d, heads, dropout):
        super().__init__()
        self.heads, self.width = heads, d // heads
        self.query, self.key = nn.Linear(d, d), nn.Linear(d, d)
        self.value, self.output = nn.Linear(d, self.width), nn.Linear(self.width, d)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        b, t, _ = x.shape
        q = self.query(x).reshape(b, t, self.heads, self.width).transpose(1, 2)
        k = self.key(x).reshape(b, t, self.heads, self.width).transpose(1, 2)
        scores = q @ k.transpose(-2, -1) / math.sqrt(self.width)
        mask = torch.triu(torch.ones(t, t, device=x.device, dtype=torch.bool), 1)
        weights = scores.masked_fill(mask, float("-inf")).softmax(-1)
        values = self.value(x).unsqueeze(1)
        attended = (self.dropout(weights) @ values).mean(1)
        return self.output(attended), weights.mean(1)


class TemporalFusionTransformer(nn.Module):
    def __init__(self, contract, config):
        super().__init__()
        d, e, drop = config["d_model"], config["embedding_dim"], config["dropout"]
        self.static = StaticEmbedding(contract["cardinalities"], e)
        self.static_selection = VariableSelection([e] * len(contract["cardinalities"]), d, drop)
        self.contexts = nn.ModuleDict({key: GRN(d, d, d, drop) for key in ("selection", "enrichment", "hidden", "cell")})
        self.encoder_selection = VariableSelection([2] * len(contract["encoder_observed"]) +
                                                  [1] * len(contract["encoder_known"]), d, drop, context=True)
        self.decoder_selection = VariableSelection([2] * len(contract["decoder_lags"]) +
                                                  [1] * len(contract["decoder_known"]), d, drop, context=True)
        self.encoder_lstm = nn.LSTM(d, d, config["lstm_layers"], batch_first=True)
        self.decoder_lstm = nn.LSTM(d, d, config["lstm_layers"], batch_first=True)
        self.layers = config["lstm_layers"]
        self.local_gate = GateAddNorm(d, drop)
        self.enrich = GRN(d, d, d, drop, d)
        self.attention = InterpretableAttention(d, config["n_heads"], drop)
        self.attention_gate = GateAddNorm(d, drop)
        self.feedforward = GRN(d, d, d, drop)
        self.final_gate = GateAddNorm(d, drop)
        self.output = nn.Linear(d, 1)

    def forward(self, inputs, return_interpretation=False):
        static, sw = self.static_selection(self.static(inputs["static_categorical"]))
        contexts = {key: layer(static) for key, layer in self.contexts.items()}

        def variables(values, masks, known):
            masked = mask_values(values, masks)
            return [torch.stack([masked[..., i], masks[..., i].float()], -1) for i in range(values.size(-1))] + list(known.split(1, -1))

        enc, ew = self.encoder_selection(variables(inputs["encoder_observed"], inputs["encoder_observed_mask"],
                                                   inputs["encoder_known"]), contexts["selection"])
        dec, dw = self.decoder_selection(variables(inputs["decoder_lags"], inputs["decoder_lag_mask"],
                                                   inputs["decoder_known"]), contexts["selection"])
        h = contexts["hidden"].unsqueeze(0).repeat(self.layers, 1, 1).contiguous()
        c = contexts["cell"].unsqueeze(0).repeat(self.layers, 1, 1).contiguous()
        enc_local, state = self.encoder_lstm(enc, (h, c))
        dec_local, _ = self.decoder_lstm(dec, state)
        local = self.local_gate(torch.cat([enc_local, dec_local], 1), torch.cat([enc, dec], 1))
        enriched = self.enrich(local, contexts["enrichment"])
        attended, aw = self.attention(enriched)
        fused = self.attention_gate(attended, enriched)
        fused = self.final_gate(self.feedforward(fused), local)
        prediction = self.output(fused[:, enc.size(1):]).squeeze(-1)
        if return_interpretation:
            return prediction, {"static_weights": sw, "encoder_weights": ew, "decoder_weights": dw,
                                "temporal_attention": aw[:, enc.size(1):]}
        return prediction


def build_network(name, contract, config):
    if name == "tft":
        return TemporalFusionTransformer(contract, config)
    if name in ("moebs_transformer", "nspace_transformer"):
        return TransformerForecast(contract, config, nspace=name == "nspace_transformer")
    raise ValueError("Unknown model architecture")
