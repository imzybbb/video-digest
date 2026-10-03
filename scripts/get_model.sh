#!/usr/bin/env bash
# 从 ModelScope 镜像下载 faster-whisper 模型（国内网络推荐）
# 用法: bash get_model.sh [目标目录]
#   默认: ./models/faster-whisper-large-v3-turbo
# 下完后用: python scripts/vd.py "<url>" --model <目标目录>
# （或者设置环境变量 VD_MODEL=<目标目录>）
set -e
D="${1:-./models/faster-whisper-large-v3-turbo}"
mkdir -p "$D"
B="https://modelscope.cn/models/pengzhendong/faster-whisper-large-v3-turbo/resolve/master"
for F in config.json model.bin preprocessor_config.json tokenizer.json vocabulary.json; do
  echo ">> $F"
  curl -L --retry 3 -o "$D/$F" "$B/$F"
done
echo "完成: $D （约 1.5GB）"
