#!/usr/bin/env bash
set -Eeuo pipefail

echo "== NVIDIA inventory =="
nvidia-smi --query-gpu=index,uuid,name,memory.total,driver_version --format=csv,noheader

echo
echo "== FFmpeg =="
ffmpeg -hide_banner -version | head -n 1
ffprobe -hide_banner -version | head -n 1

echo
echo "== NVENC encoders =="
ffmpeg -hide_banner -encoders 2>/dev/null | grep -E 'h264_nvenc|hevc_nvenc'

mapfile -t GPUS < <(nvidia-smi --query-gpu=uuid --format=csv,noheader,nounits)
for gpu in "${GPUS[@]}"; do
  echo "Testing ${gpu}"
  CUDA_VISIBLE_DEVICES="${gpu}" ffmpeg -nostdin -hide_banner -loglevel error -f lavfi -i "testsrc2=size=640x360:rate=30" -t 2 -c:v h264_nvenc -gpu 0 -preset p4 -f null -
  echo "PASS: ${gpu}"
done

