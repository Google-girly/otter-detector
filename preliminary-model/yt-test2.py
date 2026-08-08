import yt_dlp
import cv2
from ultralytics import YOLO
from ultralytics.utils.plotting import colors  # same palette plot() uses, so tags match the box

model = YOLO("../models/otter_v2.pt")

# options to control yt-dlp's behavior
ydl_opts = {
    'quiet': True,      # suppress terminal output
    'format': 'best',   # grab the best quality stream available
}

# create a yt-dlp instance with our options
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    # extract info about the stream without downloading it
    info = ydl.extract_info('https://www.youtube.com/watch?v=abbR-Ttd-cA', download=False)
    
    # pull the direct stream URL from the info dictionary
    url = info['url']

# pass the stream URL into OpenCV
cap = cv2.VideoCapture(url)


# check if OpenCV was able to open the stream
if not cap.isOpened():
    print("Error: could not open stream")

# continuously read frames from the stream

count = 0

while True:
    ret, frame = cap.read()  # read a single frame; ret is True if successful

    if not ret:
        print("Error: could not read frame")
        break
    
    # if cv2.waitKey(1) & 0xFF == ord('y'): #save on pressing 'y' 
    #     cv2.imwrite('images/c1.png',frame)
    #     print("Image saved as c1.png")

    # Perform object detection on an image
    # if count % 10 == 0:  # process every 10th frame
    #     results = model(frame, conf = 0.4)  # set confidence threshold to 0.5
    # else:
    #     results = model(frame, conf = 0.4, verbose=False)  # set confidence threshold to 0.5 and suppress output


    # run this on mac
    if count % 10 == 0:  # process every 10th frame
        results = model(frame, conf = 0.4, device='mps')  # set confidence threshold to 0.5
    else:
        results = model(frame, conf = 0.4, verbose=False, device='mps')  # set confidence threshold to 0.5 and suppress output


    new_frame = results[0].plot()  # get the first result and plot the detections on the frame

    # coord tag hanging off each box: filled in the box's own color, white text.
    # shows the top-left corner of the box. ultralytics puts its conf label above
    # the box, so this goes below.
    boxes = results[0].boxes
    if boxes is not None:
        for xyxy, cls in zip(boxes.xyxy, boxes.cls):
            x1, y1, x2, y2 = (int(v) for v in xyxy)
            color = colors(int(cls), True)  # True = BGR, matching the drawn box
            text = f"x:{x1} y:{y1}"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            tx = max(0, min(x1, new_frame.shape[1] - tw - 10))          # pull left if it'd clip off the right edge
            ty = y2 if y2 + th + 10 < new_frame.shape[0] else max(0, y2 - th - 10)  # tuck inside if the box hugs the bottom
            cv2.rectangle(new_frame, (tx, ty), (tx + tw + 10, ty + th + 10), color, -1)
            cv2.putText(new_frame, text, (tx + 5, ty + th + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    cv2.imshow('Otter Cam 2', new_frame)  # display the frame in a window
    # cv2.imshow('Otter Cam', frame)  # display the original frame in a window

    count += 1

    # press 'q' to quit
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# release the stream and close the window
cap.release()
cv2.destroyAllWindows()