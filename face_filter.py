import cv2
import numpy as np


_cascade_frontal = None
_cascade_profile = None
_cascade_eye = None


def _init_cascades():
    global _cascade_frontal, _cascade_profile, _cascade_eye
    if _cascade_frontal is None:
        _cascade_frontal = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        _cascade_profile = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_profileface.xml'
        )
        _cascade_eye = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_eye.xml'
        )


def is_human_face(img_bgr):
    """تشخیص چهره انسانی با ۳ الگوریتم"""
    try:
        _init_cascades()

        # کوچیک کن برای سرعت
        h, w = img_bgr.shape[:2]
        if w > 800:
            scale = 800 / w
            img_bgr = cv2.resize(img_bgr, (800, int(h * scale)))

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        min_size = max(40, int(min(gray.shape) * 0.08))

        # 1. روبرو
        faces = _cascade_frontal.detectMultiScale(
            gray, scaleFactor=1.05, minNeighbors=6,
            minSize=(min_size, min_size)
        )
        if len(faces) > 0:
            # چک کن حداقل یه چشم داشته باشه
            for (x, y, fw, fh) in faces:
                roi = gray[y:y + fh, x:x + fw]
                eyes = _cascade_eye.detectMultiScale(roi, 1.1, 4)
                if len(eyes) >= 1:
                    return True

        # 2. نیم‌رخ
        faces = _cascade_profile.detectMultiScale(
            gray, scaleFactor=1.05, minNeighbors=6,
            minSize=(min_size, min_size)
        )
        if len(faces) > 0:
            return True

        # 3. نیم‌رخ آینه
        flipped = cv2.flip(gray, 1)
        faces = _cascade_profile.detectMultiScale(
            flipped, scaleFactor=1.05, minNeighbors=6,
            minSize=(min_size, min_size)
        )
        if len(faces) > 0:
            return True

        return False
    except Exception as e:
        print(f"face filter error: {e}")
        return False
