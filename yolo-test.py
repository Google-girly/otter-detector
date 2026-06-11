from ultralytics import YOLO

# Load a pretrained YOLO model
model = YOLO("yolo26n.pt")

# Perform object detection on an image
results = model("/Users/detorres/Desktop/projects/people-detector/images/c1.png")

# Visualize the results
for result in results:
    result.show()