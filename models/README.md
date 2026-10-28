# Bundled models

| File | Used by | Source | SHA-256 |
|---|---|---|---|
| `face_detection_yunet_2023mar.onnx` | `kappacut.face_centres` (face-follow reframe) via `cv2.FaceDetectorYN` | [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), MIT licence | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |

If the file is missing, face detection falls back to OpenCV's bundled Haar cascade
(frontal faces only).
