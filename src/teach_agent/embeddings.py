"""本地 Embedding 模型封装（Qwen3-Embedding 系列）。

与论文/官方模型卡对齐的要点
---------------------------
- Qwen3-Embedding 是因果解码器结构，取**最后一个非 pad token** 的隐状态
  作为句向量（last-token pooling），配合 left padding；
- 向量做 L2 归一化，相似度直接用内积 / cosine；
- query 侧拼接指令 ``Instruct: {task}\\nQuery:{query}``（多语言场景官方建议
  指令用英文），document 侧不加指令；
- 模型已在本地 HuggingFace 缓存中时零下载；换 BGE-M3 需另行适配 pooling，
  本模块当前只保证 Qwen3-Embedding 系列。
"""

from __future__ import annotations

import threading

import numpy as np

from .config import (
    EMBED_BATCH_SIZE,
    EMBED_MAX_LENGTH,
    EMBED_MODEL,
    EMBED_QUERY_INSTRUCTION,
)


class LocalEmbeddings:
    def __init__(self, model_name: str = EMBED_MODEL, device_pref: str = "auto"):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        if device_pref == "auto":
            device_pref = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device_pref

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name, padding_side="left"
        )
        self.model = AutoModel.from_pretrained(model_name).to(self.device).eval()
        self.model_name = model_name

    @staticmethod
    def _last_token_pool(last_hidden_states, attention_mask):
        """官方实现：left padding 取末位；right padding 取最后一个有效 token。"""
        import torch

        left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
        if left_padding:
            return last_hidden_states[:, -1]
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[
            torch.arange(batch_size, device=last_hidden_states.device),
            sequence_lengths,
        ]

    def _encode(
        self,
        texts: list[str],
        instruction: str | None = None,
        batch_size: int = EMBED_BATCH_SIZE,
        show_progress: bool = False,
    ) -> np.ndarray:
        if instruction:
            texts = [f"Instruct: {instruction}\nQuery:{text}" for text in texts]

        all_vectors: list[np.ndarray] = []
        total = len(texts)
        for start in range(0, total, batch_size):
            batch = texts[start : start + batch_size]
            inputs = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=EMBED_MAX_LENGTH,
                return_tensors="pt",
            ).to(self.device)
            with self.torch.no_grad():
                outputs = self.model(**inputs)
            embeddings = self._last_token_pool(
                outputs.last_hidden_state, inputs["attention_mask"]
            )
            embeddings = self.torch.nn.functional.normalize(
                embeddings, p=2, dim=1
            )
            all_vectors.append(embeddings.float().cpu().numpy())
            if show_progress:
                print(
                    f"\r  embedding {min(start + batch_size, total)}/{total}",
                    end="",
                    flush=True,
                )
        if show_progress:
            print()
        return np.concatenate(all_vectors, axis=0)

    def embed_documents(self, texts: list[str], show_progress: bool = False) -> np.ndarray:
        """文档/切块编码：不加指令。"""
        return self._encode(texts, instruction=None, show_progress=show_progress)

    def embed_query(self, text: str) -> np.ndarray:
        """查询编码：加检索任务指令。"""
        return self._encode([text], instruction=EMBED_QUERY_INSTRUCTION)[0]


_singleton: LocalEmbeddings | None = None
_singleton_lock = threading.Lock()


def get_embeddings() -> LocalEmbeddings:
    """进程级单例：模型加载较慢，整个 CLI 生命周期只加载一次。

    必须加锁：Agent 的 ToolNode 会在线程池中并发执行多个工具，
    若两个线程同时首次构造，会并发首导 transformers/torch，
    在 transformers 惰性模块初始化上产生竞态（ImportError）。
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                from .config import EMBED_DEVICE

                _singleton = LocalEmbeddings(device_pref=EMBED_DEVICE)
    return _singleton
