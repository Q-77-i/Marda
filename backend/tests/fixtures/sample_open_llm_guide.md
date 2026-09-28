# RAG 基础

![RAG 流程图](/public/diagrams/rag.png)

## 一、基础概念

**Q：RAG 的完整流程是什么？** 离线：加载→切分→向量化→入库；在线：检索→重排→生成。

**Q：为什么用「向量召回 + Rerank」两段式？**  
Bi-encoder 快、负责粗筛；Cross-encoder 准、只精排少量候选。详见 [RAG 进阶](/rag/rag-advanced)。

**Q：这道题没写答案？**

## 二、分块策略

**Q：chunk 大小怎么定？** 太小语义碎、太大噪声多，一般 200~800 token 起步。
