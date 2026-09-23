# 嵌入服务镜像：本地 BGE-M3（dense + sparse）
# 独立容器而非塞进 api：api 不沾 torch（镜像小、重启不重载模型），
# 阶段 3 小内存机器可整体关掉、退回 SiliconFlow。
# ⚠️ 模型权重 build 时下载进镜像（约 2.3GB）：compose up 即可用，重建走层缓存。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    HF_ENDPOINT=https://hf-mirror.com

# torch 先单独装、且只从 CPU 索引装（+cpu 版本号只在专用索引上有）：
# 先装好它，下一步 FlagEmbedding 的 torch>=2.4 就已满足，不会再去 PyPI 拉 CUDA 版
RUN pip install --no-cache-dir \
      --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cpu/ \
      torch==2.9.1+cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 权重烤进镜像（而非首启下载）
# FlagEmbedding 的 snapshot_download 只忽略 flax/rust/tf，onnx/ 那 2.2GB 导出会一起拉下来
# （我们只用 PyTorch 权重）→ 装完即删，镜像小 2.2GB
RUN python -c "from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('BAAI/bge-m3', use_fp16=False)" \
 && KEEP=$(readlink -f /models/hub/models--BAAI--bge-m3/snapshots/*/pytorch_model.bin) \
 && rm -rf /models/hub/models--BAAI--bge-m3/snapshots/*/onnx \
 && find /models/hub/blobs -type f -size +1G ! -samefile "$KEEP" -delete

COPY app.py .

EXPOSE 8091
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8091"]
