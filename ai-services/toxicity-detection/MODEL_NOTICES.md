# Model notice

Live speech moderation uses `Roblox/voice-safety-classifier-v2`, a pretrained
voice safety classifier distributed under CC BY-SA 3.0. The model is loaded
from Hugging Face on first audio moderation request. See the
[model card](https://huggingface.co/Roblox/voice-safety-classifier-v2) and
[upstream inference code](https://github.com/Roblox/voice-safety-classifier)
for attribution, license terms, supported languages, and evaluation details.

The model was evaluated on Roblox voice-chat data, not MeetIQ meetings. Its
published English recall is limited, and its best results are reported on
15-second audio segments; MeetIQ currently sends shorter chunks. Treat its
scores as one moderation signal and validate them on MeetIQ audio before
relying on them for high-impact decisions.
