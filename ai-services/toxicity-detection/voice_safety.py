"""Adapter for Roblox's pretrained voice safety classifier v2."""

from functools import lru_cache

import librosa
import numpy as np
import torch
from transformers import WavLMForSequenceClassification


MODEL_ID = "Roblox/voice-safety-classifier-v2"
LABELS = (
    "discrimination",
    "harassment",
    "sexual",
    "illegal_and_regulated",
    "dating_and_romantic",
    "profanity",
)
SAMPLE_RATE = 16_000


@lru_cache(maxsize=1)
def _load_model():
    """Download once on first use, then reuse the model for later chunks."""
    model = WavLMForSequenceClassification.from_pretrained(
        MODEL_ID,
        num_labels=len(LABELS),
    )
    model.eval()
    return model


def detect_audio_toxicity(audio_path):
    """Return pretrained audio-level toxicity probabilities for one clip."""
    audio, _ = librosa.load(audio_path, sr=SAMPLE_RATE, mono=True)
    if audio.size == 0:
        return {label: 0.0 for label in LABELS}

    inputs = torch.from_numpy(np.asarray(audio, dtype=np.float32)).unsqueeze(0)
    model = _load_model()
    with torch.inference_mode():
        logits = model(inputs).logits
        probabilities = torch.sigmoid(logits)[0].cpu().tolist()

    return {
        label: float(probabilities[index])
        for index, label in enumerate(LABELS)
    }
