import torch
from torch import nn

from viswsl.modules.embedding import WordAndPositionalEmbedding


class TransformerTextualStream(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        hidden_size: int,
        feedforward_size: int,
        attention_heads: int,
        num_layers: int,
        dropout: float = 0.1,
        norm_type: str = "pre",
        activation: str = "gelu",
        padding_idx: int = 0,
    ):
        super().__init__()
        self.vocab_size = vocab_size
        self.hidden_size = hidden_size
        self.feedforward_size = feedforward_size
        self.attention_heads = attention_heads
        self.num_layers = num_layers
        self.padding_idx = padding_idx

        self.embedding = WordAndPositionalEmbedding(
            self.vocab_size, self.textual_feature_size, dropout=dropout
        )

        # Make encoder layer depending on whether it's a Pre-Norm or Post-Norm.
        _EncoderLayerClass = (
            nn.TransformerEncoderLayer
            if norm_type == "post"
            else PreNormTransformerEncoderLayer
        )
        _encoder_layer = _EncoderLayerClass(
            self.textual_feature_size,
            self.attention_heads,
            dim_feedforward=self.feedforward_size,
            dropout=dropout,
            activation=activation,
        )
        self.encoder = nn.TransformerEncoder(_encoder_layer, self.num_layers)
        self.apply(self.init_weights)

    @property
    def textual_feature_size(self):
        return self.hidden_size

    @staticmethod
    def init_weights(module):
        r"""Initialize weights like BERT - N(0.0, 0.02), bias = 0."""

        if isinstance(module, nn.Linear):
            module.weight.data.normal_(mean=0.0, std=0.02)
        elif isinstance(module, nn.MultiheadAttention):
            module.in_proj_weight.data.normal_(mean=0.0, std=0.02)
            module.out_proj.weight.data.normal_(mean=0.0, std=0.02)
        elif isinstance(module, nn.Embedding):
            module.weight.data.normal_(mean=0.0, std=0.02)
            if module.padding_idx is not None:
                module.weight.data[module.padding_idx].zero_()

    def forward(self, caption_tokens: torch.LongTensor) -> torch.Tensor:

        # Form a mask, it is True for positions with padding token.
        # Transformer will ignore these positions for multi-headed attention.
        caption_mask = caption_tokens == self.padding_idx

        # shape: (batch_size, max_caption_length, embedding_size)
        token_embeddings = self.embedding(caption_tokens)

        # `TransformerEncoder` requires the sequence input as
        # (max_caption_length, batch_size, hidden_size). So we transpose the
        # first two dimensions of token embeddings, pass through encoder, and
        # later undo the transpose.
        token_embeddings = token_embeddings.transpose(0, 1)

        # shape: (max_caption_length, batch_size, hidden_size)
        textual_features = self.encoder(
            token_embeddings, src_key_padding_mask=caption_mask
        )
        # shape: (batch_size, max_caption_length, hidden_size)
        textual_features = textual_features.transpose(0, 1)

        return textual_features


class PreNormTransformerEncoderLayer(nn.TransformerEncoderLayer):

    def forward(self, src, src_mask=None, src_key_padding_mask=None):
        # fmt: off
        src2 = self.norm1(src)
        src2, _ = self.self_attn(
            src2, src2, src2, attn_mask=src_mask,
            key_padding_mask=src_key_padding_mask,
        )
        src = src + self.dropout1(src2)

        src2 = self.norm2(src)
        src2 = self.linear2(self.dropout(self.activation(self.linear1(src2))))
        src = src + self.dropout2(src2)
        # fmt: on
        return src
