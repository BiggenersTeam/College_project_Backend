"""
FaceTracer Face Recognition Service
=====================================
OpenCV DNN-based face detection and recognition using SFace model.
Falls back to pixel histogram matching when DNN models are unavailable.
"""
import os
import logging
import numpy as np
from typing import Optional, List

logger = logging.getLogger(__name__)

# Paths to ONNX model files
_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
_DETECTOR_PATH   = os.path.join(_MODELS_DIR, "face_detection_yunet_2023mar.onnx")
_RECOGNIZER_PATH = os.path.join(_MODELS_DIR, "face_recognition_sface_2021dec.onnx")


class FaceRecognitionService:
    """
    Singleton service for face detection and recognition.
    Uses OpenCV DNN with YuNet detector and SFace recognizer.
    """

    def __init__(self):
        self.detector   = None
        self.recognizer = None
        self.cascade    = None
        self.dnn_active = False
        self._initialize()

    def _initialize(self):
        try:
            import cv2
            # Load YuNet face detector
            if os.path.exists(_DETECTOR_PATH):
                self.detector = cv2.FaceDetectorYN.create(
                    _DETECTOR_PATH, "", (320, 320),
                    score_threshold=0.6,
                    nms_threshold=0.3,
                    top_k=5000,
                )
            # Load SFace face recognizer
            if os.path.exists(_RECOGNIZER_PATH):
                self.recognizer = cv2.FaceRecognizerSF.create(
                    _RECOGNIZER_PATH, ""
                )
            if self.detector and self.recognizer:
                self.dnn_active = True
                logger.info("OpenCV DNN Face Recognition models initialized successfully.")
            else:
                raise FileNotFoundError("One or both ONNX model files not found.")
        except Exception as e:
            logger.warning(
                f"DNN face recognition unavailable ({e}). "
                f"Falling back to pixel histogram matching."
            )
            self._load_fallback()

    def _load_fallback(self):
        """Load OpenCV Haar cascade as fallback face detector."""
        try:
            import cv2
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            if os.path.exists(cascade_path):
                self.cascade = cv2.CascadeClassifier(cascade_path)
                logger.info("Fallback Haar cascade face detector loaded.")
        except Exception as e:
            logger.warning(f"Fallback cascade also unavailable: {e}")

    # ── Face Encoding ───────────────────────────────────────────────────────
    def get_face_encoding(self, image_path: str) -> Optional[List[float]]:
        """
        Extract a face encoding vector from an image file.

        Returns:
            List[float] — 128-dim (DNN) or 1024-dim (fallback) encoding
            None — if no face detected
        """
        if self.dnn_active:
            return self._dnn_encoding(image_path)
        return self._fallback_encoding(image_path)

    def _dnn_encoding(self, image_path: str) -> Optional[List[float]]:
        try:
            import cv2
            img = cv2.imread(image_path)
            if img is None:
                logger.warning(f"Could not read image: {image_path}")
                return None

            h, w = img.shape[:2]
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(img)

            if faces is None or len(faces) == 0:
                logger.info(f"No face detected in {os.path.basename(image_path)}")
                return None

            # Use the highest-confidence face
            best_face = max(faces, key=lambda f: f[-1])
            aligned = self.recognizer.alignCrop(img, best_face)
            feature = self.recognizer.feature(aligned)
            return feature.flatten().tolist()

        except Exception as e:
            logger.error(f"DNN face encoding error for {image_path}: {e}")
            return None

    def _fallback_encoding(self, image_path: str) -> Optional[List[float]]:
        """
        Pixel-histogram based fallback encoding.
        Less accurate but works without ONNX models.
        Returns 1024-dim encoding.
        """
        try:
            import cv2
            img = cv2.imread(image_path)
            if img is None:
                return None

            # Try cascade detection first
            face_roi = None
            if self.cascade:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                faces = self.cascade.detectMultiScale(gray, 1.1, 4, minSize=(30, 30))
                if len(faces) > 0:
                    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                    face_roi = img[y:y+fh, x:x+fw]

            if face_roi is None:
                face_roi = img

            # Compute colour histogram as encoding
            resized = cv2.resize(face_roi, (32, 32))
            encoding = []
            for channel in range(3):
                hist = cv2.calcHist([resized], [channel], None, [256//8], [0, 256])
                encoding.extend(hist.flatten().tolist())

            # Pad / trim to exactly 1024 dims
            if len(encoding) < 1024:
                encoding += [0.0] * (1024 - len(encoding))
            encoding = encoding[:1024]
            return encoding

        except Exception as e:
            logger.error(f"Fallback encoding error for {image_path}: {e}")
            return None

    # ── Face Matching ───────────────────────────────────────────────────────
    def match_faces(
        self,
        query_encoding_list: List[float],
        database_encodings_list: List[float],
    ):
        """
        Compare two face encoding vectors.

        Returns:
            (cosine_similarity: float, confidence_percentage: float)
        """
        if not query_encoding_list or not database_encodings_list:
            return 0.0, 0.0

        q_arr  = np.array(query_encoding_list,        dtype=np.float32)
        db_arr = np.array(database_encodings_list,    dtype=np.float32)

        # Handle dimension mismatch (DNN vs fallback)
        if q_arr.shape != db_arr.shape:
            min_len = min(len(q_arr), len(db_arr))
            q_arr   = q_arr[:min_len]
            db_arr  = db_arr[:min_len]

        q_norm  = np.linalg.norm(q_arr)
        db_norm = np.linalg.norm(db_arr)
        if q_norm == 0 or db_norm == 0:
            return 0.0, 0.0

        similarity = float(np.dot(q_arr, db_arr) / (q_norm * db_norm))

        if self.dnn_active:
            # SFace: ≥0.363 = same person
            threshold = 0.363
            if similarity >= threshold:
                confidence = 70.0 + ((similarity - threshold) / (1.0 - threshold)) * 29.9
            else:
                confidence = max(0.0, (similarity + 0.1) / (threshold + 0.1) * 69.9)
        else:
            # Pixel fallback: lower threshold
            threshold = 0.55
            if similarity >= threshold:
                confidence = 50.0 + ((similarity - threshold) / (1.0 - threshold)) * 49.9
            else:
                confidence = max(0.0, (similarity / threshold) * 49.9)

        return similarity, round(confidence, 2)


# ── Singleton instance (imported by routers) ─────────────────────────────────
face_recognition_service = FaceRecognitionService()
