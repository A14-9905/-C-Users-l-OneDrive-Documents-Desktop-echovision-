import cv2
import time
import subprocess
from collections import defaultdict
from ultralytics import YOLO

# =========================================================
# FAST CONFIG
# =========================================================
MODEL_PATH = "/home/echovision/camera_project/yolov8n.pt"   # fastest among good YOLOv8 models
CAMERA_INDEX = 0

MIN_CONFIDENCE = 0.50
IOU_THRESHOLD = 0.40
IMG_SIZE = 640               # lower = faster
COOLDOWN = 3.0
MAX_DETECTIONS = 6

FRAME_WIDTH = 640            # lower resolution = faster
FRAME_HEIGHT = 480
PROCESS_EVERY_N_FRAMES = 2   # detect every 2nd frame for speed

SPEECH_SPEED = "150"

IMPORTANT_OBJECTS = {
    "person": "Person",
    "bottle": "Bottle",
    "car": "Car",
    "bus": "Bus",
    "truck": "Truck",
    "motorcycle": "Motorcycle",
    "bicycle": "Bicycle",
    "chair": "Chair",
    "dog": "Dog",
    "cat": "Cat",
    "keyboard": "Keyboard",
    "tv": "TV",
    "laptop": "Laptop",
    "cell phone": "Phone",
    "bench": "Bench",
    "book": "Book",
    "watch": "watch"
}

def speak(text: str):
    try:
        subprocess.Popen(
            ["espeak-ng", "-s", SPEECH_SPEED, text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print("Speech error:", e)

def get_direction(center_x: float, width: int) -> str:
    if center_x < width * 0.35:
        return "left"
    elif center_x > width * 0.65:
        return "right"
    return "center"

def get_distance_label(box_area: float, frame_area: float) -> str:
    ratio = box_area / frame_area
    if ratio > 0.20:
        return "very close"
    elif ratio > 0.08:
        return "close"
    else:
        return "far"

def get_box_color(distance_label: str):
    if distance_label == "very close":
        return (0, 0, 255)
    elif distance_label == "close":
        return (0, 165, 255)
    return (0, 255, 0)

def main():
    print("Loading YOLO model...")
    model = YOLO(MODEL_PATH)

    print("Opening camera...")
    cap = cv2.VideoCapture(CAMERA_INDEX)

    if not cap.isOpened():
        print("Error: camera not opened")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    last_spoken = defaultdict(float)
    frame_count = 0
    cached_boxes = []

    print("Fast detection started. Press q to exit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Camera read failed")
            time.sleep(0.1)
            continue

        frame_count += 1
        now = time.time()
        height, width = frame.shape[:2]
        frame_area = width * height

        # Run YOLO only on selected frames
        if frame_count % PROCESS_EVERY_N_FRAMES == 0:
            results = model.predict(
                source=frame,
                conf=MIN_CONFIDENCE,
                iou=IOU_THRESHOLD,
                imgsz=IMG_SIZE,
                max_det=MAX_DETECTIONS,
                verbose=False
            )

            new_boxes = []
            best_candidate = None
            best_score = -1

            for r in results:
                if r.boxes is None:
                    continue

                for box in r.boxes:
                    conf = float(box.conf[0])
                    cls_id = int(box.cls[0])
                    label = model.names[cls_id]

                    if label not in IMPORTANT_OBJECTS:
                        continue

                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    x1 = max(0, x1)
                    y1 = max(0, y1)
                    x2 = min(width - 1, x2)
                    y2 = min(height - 1, y2)

                    box_width = max(1, x2 - x1)
                    box_height = max(1, y2 - y1)
                    box_area = box_width * box_height

                    center_x = (x1 + x2) / 2
                    direction = get_direction(center_x, width)
                    distance_label = get_distance_label(box_area, frame_area)
                    color = get_box_color(distance_label)
                    display_name = IMPORTANT_OBJECTS[label]

                    new_boxes.append({
                        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                        "text": f"{display_name} | {direction} | {distance_label}",
                        "color": color
                    })

                    score = (box_area / frame_area) * 100 + conf
                    if score > best_score:
                        best_score = score
                        best_candidate = {
                            "label": label,
                            "display_name": display_name,
                            "direction": direction,
                            "distance_label": distance_label
                        }

            cached_boxes = new_boxes

            if best_candidate is not None:
                key = f"{best_candidate['label']}:{best_candidate['direction']}:{best_candidate['distance_label']}"
                if now - last_spoken[key] > COOLDOWN:
                    message = (
                        f"{best_candidate['display_name']} "
                        f"on {best_candidate['direction']}, "
                        f"{best_candidate['distance_label']}"
                    )
                    print("Speaking:", message)
                    speak(message)
                    last_spoken[key] = now

        # Draw last detections on every frame
        for item in cached_boxes:
            cv2.rectangle(
                frame,
                (item["x1"], item["y1"]),
                (item["x2"], item["y2"]),
                item["color"],
                2
            )
            cv2.putText(
                frame,
                item["text"],
                (item["x1"], max(25, item["y1"] - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                item["color"],
                2
            )

        cv2.putText(
            frame,
            "Fast Detection | Press Q to exit",
            (15, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2
        )

        cv2.imshow("Fast Smart Camera Detection", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
