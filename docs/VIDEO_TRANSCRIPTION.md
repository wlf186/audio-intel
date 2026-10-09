# 音视频转写 / Audio and video transcription

工作台分为「新建音频转写」「新建视频转写」「任务与结果」。音频与视频共用 ASR 队列，使用相同的模型、设备、热词和说话人能力。视频入口额外提供 PPT 区域框选和画面分析。

The workspace has separate audio/video creation tabs and a shared task/result tab. Both use the same ASR queue, models, devices, hotwords and speaker features. Video adds slide-region selection and presentation analysis.

## 上传 / Uploads

- 音频 / Audio: WAV, MP3, FLAC, M4A, AAC, OGG, OPUS, AIFF; 默认 / default **4 GiB** (`AUDIO_INTEL_MAX_UPLOAD_BYTES`).
- 视频 / Video: MP4, M4V, MOV, MKV, WEBM, AVI, MPEG, MPG, TS; 默认 / default **25 GiB = 26,843,545,600 bytes** (`AUDIO_INTEL_MAX_VIDEO_UPLOAD_BYTES`).容器必须有可解码的画面和音轨 / A decodable video track and audio track are required.
- 实际限制以 `GET /api/v1/capabilities` 的 `limits.max_upload_bytes` 和 `asr.video` 为准；容器扩展名不保证其中所有编解码器都可用。原音频 API 保持对音视频输入的兼容和原有上限；获得画面分析与 25 GiB 上限须使用视频 API。

Capabilities are authoritative; a container extension does not guarantee every codec within it is supported. The original audio API keeps its audio/video input compatibility and original limit. Use the new video API for presentation analysis and the 25 GiB limit.

视频采用顺序 32 MiB 分片，末片为剩余字节。暂停、断网或服务重启后，重新选择同一文件即可继续；浏览器重新计算已确认部分的 SHA-256，防止误接另一个文件。未完成上传最多 8 个，按剩余字节预留磁盘额度；保留磁盘安全水位。上传完成后才占用队列位置，队列满时可稍后再次提交。

Video uses sequential 32 MiB chunks, with a smaller final remainder. After pause, network loss or server restart, reselect the same file; confirmed chunks are verified against SHA-256 hashes before resuming. Up to eight unfinished uploads reserve their remaining bytes against disk admission. A completed upload takes a queue slot only when submitted; retry submission if the queue is full.

未完成上传闲置 **24 小时**或创建满 **7 天**后到期，每 15 分钟清理一次；「清理未完成上传」立即删除服务端残留。浏览器仅在 **sessionStorage** 保存上传 ID、幂等键、文件名/大小和提交设置，不保存文件内容；刷新保留，关闭页签或退出登录后清除浏览器记录，服务端仍按到期策略清理。已提交任务由普通任务删除机制管理。多页签不要同时续传同一上传。

Unfinished uploads expire after **24 idle hours** or **7 days from creation**, and are swept every 15 minutes. Explicit discard removes received bytes immediately. Browser **sessionStorage** holds only IDs, idempotency keys, filename/size and settings, never file bytes: reload preserves it; closing the tab or logging out clears the browser journal. Server expiry still applies. Submitted inputs belong to ordinary tasks. Avoid resuming one upload from multiple tabs simultaneously.

## 画面与讨论 / Images and discussion

面向 PPT 讲解、屏幕录制。使用 PyAV 内置的 FFmpeg `scdet`，按约 0.5 秒抽样，在内存中比较感知哈希、文字边缘保留率和稳定性；同一页逐步显示内容尽量合并，保留最后的稳定完整画面。非相邻的重复页面保留各自时间范围。默认最多 2,000 段，超过时明确失败并提示拆分源文件。这里的「关键帧」指内容代表画面，不是视频编码的 I 帧。

Designed for slides and screen recordings. PyAV's FFmpeg `scdet` is complemented by in-memory perceptual hashes, edge retention and stability checks sampled about every 0.5 seconds. Incremental slide builds are merged where possible, retaining the last stable frame. Nonadjacent repeated pages retain separate time ranges. More than 2,000 sections fails explicitly with a request to split the source. These are content representatives, not codec I-frames.

视频时刻依据媒体 PTS，与音轨共用起点；音轨延迟以静音补齐，不按帧序号推算时间。跨页发言有完整可靠的逐词对齐时按词分配，否则整句归属时间重叠最多的一页，保持原文、不丢句。画面分析是启发式算法，不做 OCR 或语义理解；短于抽样/稳定窗口的页面、相似模板、复杂动画、镜头移动可能需要修正。带讲者小窗时可先框选 PPT 区域。

Media PTS provides a shared audio/video origin; delayed audio is padded with silence. Speech crossing a page boundary is split at aligned words only when alignment covers the original text; otherwise the whole sentence goes to the page with greatest time overlap. No text is dropped. Detection is heuristic, without OCR or semantic understanding. Very short pages, similar templates, complex animation or camera movement may need correction. Crop to the slide region when a speaker inset interferes.

结果支持与下一段合并、拆分、调整结束时间、更换截图和恢复自动分段。修订号避免覆盖其他客户端的修改；遇到冲突，关闭编辑器并重新加载分段。原始逐句文本与说话人快照保持可查看。

Results support merge-next, split, boundary adjustment, replacement images and restoration of automatic sections. Revision checks prevent overwriting another client's edits; on conflict, close the editor and reload sections. Original detailed text and speaker snapshots remain accessible.

## PDF 与磁盘 / PDF and disk use

音频提供主操作「导出简洁 PDF」，次级「明细与字幕」保留 JSON、TXT、SRT、VTT。简洁 PDF 将相同 speaker ID 的相邻发言合并为一段，保留第一句开始与最后一句结束时间，不删改口头语、不做总结；同名但不同 ID 的说话人不会误合并。视频仅提供图文简洁 PDF，按画面分段，画面下展示相应讨论。PDF 内嵌本地简体中文字体，无云服务或系统字体依赖。

Audio offers a primary concise PDF action and a secondary details/subtitles menu. Adjacent speech with the same speaker ID merges with its combined time range; words and disfluencies are preserved. Equal names with different IDs do not merge. Video offers illustrated concise PDF, with images followed by their discussion. A bundled Chinese font keeps PDF rendering offline.

上传直接顺序追加到一份文件，校验并 fsync 后才确认偏移；不创建分片文件再拼接、不预分配 25 GiB，不在重试时重写已确认数据。提交时同盘重命名。画面分析只保存最终代表 JPEG，不保存抽样帧序列。只生成一份归一化音频供推理与回放复用；临时目录和数据目录在同一文件系统时完成阶段直接移动，否则跨盘移动需一次复制。失败/取消后的工作目录由现有 worker 清理。PDF 按需在内存生成，单渲染并发、64 MiB LRU 缓存（5 分钟）；单份 PDF 输入上限为 400 万文本字符、128 MiB JPEG，超过返回 413 并提示拆分。预览最多两个解码器且不写磁盘。修改截图后清理不再被当前/原始分段引用的图片，删除任务清理其输入与结果。

Uploads append to one file and are acknowledged only after checksum verification and fsync: no chunk concatenation, full-size preallocation or acknowledged-byte rewrite. Submission renames on the same filesystem. Only final JPEG representatives are written; sampled frames stay in memory. One normalized audio file serves inference and playback. Keep temporary and data directories on the same filesystem for rename-only completion; crossing filesystems requires one copy. Existing worker cleanup removes failed/cancelled working directories. PDFs render on demand in memory, with one renderer and a 64 MiB, five-minute LRU. PDF inputs are limited to 4 million text characters and 128 MiB of JPEGs; larger exports return 413 with a split-source hint. At most two preview decoders run, without writing images. Unreferenced edited images are removed, and task deletion removes owned inputs/results.

算法选择参考：[FFmpeg scdet](https://ffmpeg.org/ffmpeg-filters.html#scdet)、[PySceneDetect 检测器](https://www.scenedetect.com/docs/api/detectors.html)。后者提供内容/自适应切镜，但不直接判断 PPT 的同页动画，因此本实现复用现有 FFmpeg，加上面向幻灯片的比较，避免增加 OpenCV/模型运行时。

## 客户端示例 / Client example

```bash
AUDIO_INTEL_BASE_URL=http://127.0.0.1:20810 \
  .runtime/api/bin/python scripts/submit_video.py lecture.mp4 --state tmp/lecture-upload.json --pdf tmp/lecture.pdf
```

Windows 使用 `.runtime\api\Scripts\python.exe`。可选 `--device cpu` / `--device gpu`；省略则采用部署默认。鉴权从 `AUDIO_INTEL_API_KEY` 读取。中断后使用同一 `--state` 和原文件重跑；收到 429 时按 `Retry-After` 等待后重跑。上传过期后使用新的状态文件。完整接口见 [API 文档](API.md) 与本地 `/docs`。

On Windows use `.runtime\api\Scripts\python.exe`. Optional `--device` overrides the deployment default. Authentication comes from `AUDIO_INTEL_API_KEY`. Rerun with the same state and file after interruption; on 429, wait for `Retry-After` before rerunning. Use a new journal after expiry. See [API documentation](API.md) and local `/docs`.

## 界面示例 / Interface examples

以下使用两页合成测试视频与 mock 转写内容，展示图文阅读与编辑控件；不是实际会议数据。 / These screenshots use a two-slide synthetic test video and mock transcript, not a real meeting.

![桌面视频转写结果 / Desktop video transcript](assets/video-transcription/result-1440.png)

![390px 视频转写结果 / 390px video transcript](assets/video-transcription/result-390.png)
