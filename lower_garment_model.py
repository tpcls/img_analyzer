import json
import math
from pathlib import Path


MODEL_VERSION = 1
DEFAULT_MODEL_PATH = Path(__file__).with_name("lower_garment_model.json")

NUMERIC_FEATURES = [
    "skin_ratio",
    "upper_skin_ratio",
    "lower_skin_ratio",
    "lower_coverage_ratio",
    "lower_split_ratio",
    "lower_center_fill_ratio",
    "person_confidence",
    "color_confidence",
]

CATEGORICAL_FEATURES = {
    "lower_garment": [
        "unknown",
        "shorts",
        "mini_skirt",
        "knee_length_pants",
        "knee_length_skirt",
        "cropped_pants",
        "midi_skirt",
        "long_pants",
        "long_skirt",
    ],
    "lower_garment_family": ["unknown", "pants", "skirt"],
    "pants_length": ["unknown", "shorts", "knee_length", "cropped", "long"],
    "exposure": ["low", "medium", "high"],
    "upper_color": ["unknown", "black", "white", "gray", "pink", "purple", "red", "orange", "blue", "green"],
    "lower_color": ["unknown", "black", "white", "gray", "pink", "purple", "red", "orange", "blue", "green"],
}

PANTS_GARMENTS = {"shorts", "knee_length_pants", "cropped_pants", "long_pants"}
SKIRT_GARMENTS = {"mini_skirt", "knee_length_skirt", "midi_skirt", "long_skirt"}


def lower_garment_family(label):
    if label in PANTS_GARMENTS:
        return "pants"
    if label in SKIRT_GARMENTS:
        return "skirt"
    return "unknown"


def pants_length_for_label(label):
    if label in {"shorts", "mini_skirt"}:
        return "shorts"
    if label in {"knee_length_pants", "knee_length_skirt"}:
        return "knee_length"
    if label in {"cropped_pants", "midi_skirt"}:
        return "cropped"
    if label in {"long_pants", "long_skirt"}:
        return "long"
    return "unknown"


def feature_names():
    names = list(NUMERIC_FEATURES)
    for key, values in CATEGORICAL_FEATURES.items():
        names.extend(f"{key}={value}" for value in values)
    return names


FEATURE_NAMES = feature_names()


def vectorize_analysis(analysis):
    values = []
    for key in NUMERIC_FEATURES:
        values.append(float(analysis.get(key, 0.0) or 0.0))
    for key, options in CATEGORICAL_FEATURES.items():
        value = analysis.get(key) or "unknown"
        values.extend(1.0 if value == option else 0.0 for option in options)
    return values


def softmax(logits):
    if not logits:
        return []
    offset = max(logits)
    exp_values = [math.exp(value - offset) for value in logits]
    total = sum(exp_values)
    return [value / total for value in exp_values]


def train_softmax(samples, classes=None, epochs=2400, lr=0.18, l2=0.002):
    if not samples:
        raise ValueError("no training samples")
    classes = classes or sorted({label for _features, label in samples})
    class_index = {label: index for index, label in enumerate(classes)}
    xs = [features for features, _label in samples]
    ys = [class_index[label] for _features, label in samples]
    feature_count = len(xs[0])
    means = []
    scales = []
    for col in range(feature_count):
        values = [row[col] for row in xs]
        mean = sum(values) / len(values)
        var = sum((value - mean) ** 2 for value in values) / len(values)
        scale = math.sqrt(var) or 1.0
        means.append(mean)
        scales.append(scale)
    xs = [[(value - means[i]) / scales[i] for i, value in enumerate(row)] for row in xs]
    weights = [[0.0] * (feature_count + 1) for _ in classes]
    counts = [0] * len(classes)
    for index in ys:
        counts[index] += 1
    class_weights = [len(samples) / (len(classes) * max(1, count)) for count in counts]

    for epoch in range(epochs):
        rate = lr / (1.0 + epoch / 900.0)
        grad = [[0.0] * (feature_count + 1) for _ in classes]
        for row, target in zip(xs, ys):
            logits = []
            for class_weights_row in weights:
                logits.append(class_weights_row[0] + sum(w * x for w, x in zip(class_weights_row[1:], row)))
            probs = softmax(logits)
            sample_weight = class_weights[target]
            for class_id, prob in enumerate(probs):
                diff = (prob - (1.0 if class_id == target else 0.0)) * sample_weight
                grad[class_id][0] += diff
                for i, x in enumerate(row):
                    grad[class_id][i + 1] += diff * x
        denom = float(len(samples))
        for class_id in range(len(classes)):
            for i in range(feature_count + 1):
                penalty = l2 * weights[class_id][i] if i else 0.0
                weights[class_id][i] -= rate * ((grad[class_id][i] / denom) + penalty)

    return {
        "version": MODEL_VERSION,
        "classes": classes,
        "feature_names": FEATURE_NAMES,
        "means": means,
        "scales": scales,
        "weights": weights,
        "temperature": 2.5,
        "training_samples": len(samples),
    }


def predict(model, analysis):
    raw = vectorize_analysis(analysis)
    row = [
        (value - model["means"][i]) / (model["scales"][i] or 1.0)
        for i, value in enumerate(raw)
    ]
    temperature = float(model.get("temperature", 1.0) or 1.0)
    logits = []
    for class_weights_row in model["weights"]:
        logits.append((class_weights_row[0] + sum(w * x for w, x in zip(class_weights_row[1:], row))) / temperature)
    probs = softmax(logits)
    best_index = max(range(len(probs)), key=lambda index: probs[index])
    return {
        "label": model["classes"][best_index],
        "confidence": round(probs[best_index], 4),
        "probabilities": {
            label: round(prob, 4)
            for label, prob in zip(model["classes"], probs)
        },
    }


def load_model(path=DEFAULT_MODEL_PATH):
    model_path = Path(path)
    if not model_path.exists():
        return None
    with model_path.open("r", encoding="utf-8") as fp:
        model = json.load(fp)
    if model.get("version") != MODEL_VERSION:
        return None
    if model.get("feature_names") != FEATURE_NAMES:
        return None
    return model


def save_model(model, path=DEFAULT_MODEL_PATH):
    model_path = Path(path)
    with model_path.open("w", encoding="utf-8") as fp:
        json.dump(model, fp, ensure_ascii=False, indent=2, sort_keys=True)
        fp.write("\n")
