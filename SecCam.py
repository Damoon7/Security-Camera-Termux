#!/usr/bin/env python3
import os
from PIL import Image, ImageChops
import numpy as np
from datetime import datetime
from argparse import ArgumentParser
from time import sleep, time
from subprocess import Popen, PIPE, DEVNULL, STDOUT, run
from statistics import stdev, mean


def dif_of_images(image1, image2):
    diff = ImageChops.difference(image1, image2)
    return int(np.asarray(diff, dtype=np.int64).sum())


def processCommand(command, shellVal, stout, sterr, err):
    try:
        process = Popen(command, shell=shellVal, stdout=stout, stderr=sterr)
        process.communicate()
    except Exception:
        print(err)


def take_processed_photo(camera_id, filename):
    processCommand(f"termux-camera-photo -c {camera_id} {filename}", True, PIPE, PIPE, "Can't take photo")
    if not os.path.exists(filename):
        return None
    with Image.open(filename) as img:
        processed = img.resize((436, 580)).convert('L')
    os.remove(filename)
    return processed


def setup_num(camera_id, setup_photos=20):
    imgArray = []
    delta = []
    for i in range(setup_photos):
        print(f"Calibrating camera: photo {i+1}/{setup_photos}...", end="\r")
        img = take_processed_photo(camera_id, 'img_set.jpeg')
        if img is not None:
            imgArray.append(img)
        sleep(1)

    for x in range(len(imgArray)):
        for y in range(x + 1, len(imgArray)):
            delta.append(dif_of_images(imgArray[x], imgArray[y]))

    if not delta:
        return 100000, None

    maxdel = max(delta)
    std = stdev(delta) if len(delta) > 1 else 0
    avg = mean(delta)
    print('\nDevice is set up!                ')

    threshold = maxdel * (1 + std / avg) if avg > 0 else maxdel + 10000
    return threshold, imgArray[-1]


def send_photo_to_telegram(Chat_ID, Bot_Token, path, image):
    filepath = os.path.join(path, image)
    cmd = f'curl -s -X POST -F chat_id="{Chat_ID}" -F photo=@"{filepath}" https://api.telegram.org/bot{Bot_Token}/sendPhoto'
    processCommand(cmd, True, DEVNULL, STDOUT, "Can't send to telegram")


def countdown(t):
    while t > 0:
        mins, secs = divmod(t, 60)
        print(f'{mins:02d}:{secs:02d}', end="\r")
        sleep(1)
        t -= 1


def clock():
    cl = datetime.now().strftime("%H:%M:%S")
    print(cl, end="\r")


# Clear the screen
run("clear", shell=True)

# Arguments:
parser = ArgumentParser()
parser.add_argument('-C', '--camera', help='0 for back Camera, 1 for front Camera', type=str, default='0')
parser.add_argument('-v', '-V', '--video', help='1 to convert photos to video file and 0 for dont convert', type=int, default=0)
parser.add_argument('-sms', '--sms', help='Enter phone number to send sms')
parser.add_argument('-c', '--call', help='Enter phone number to call')
parser.add_argument('-n', '--number', help='number of photos you want to be captured', type=int, default=50)
parser.add_argument('-s', '--seconds', help='Seconds needs to set the device', type=int, default=30)
parser.add_argument('-ch', '--chatid', help='your chat id', type=str)
parser.add_argument('-bt', '--bottoken', help='your telegram bot token', type=str)
args = parser.parse_args()

# Wake up Termux while phone is locked:
run("termux-wake-lock", shell=True)

countdown(args.seconds)
print('Setting up the device...\n')

delta, last_img = setup_num(args.camera)
if last_img is None:
    last_img = take_processed_photo(args.camera, 'photo.jpeg')

imgArr = [last_img]
sleep(1)

while True:
    new_img = take_processed_photo(args.camera, 'photo.jpeg')
    if new_img is None:
        sleep(1)
        continue

    imgArr.append(new_img)
    dif = dif_of_images(imgArr[0], imgArr[1])
    imgArr.pop(0)
    sleep(1)
    clock()

    if dif > delta:
        print('\nAn External Object Detected')

        if args.sms is not None:
            processCommand(f'termux-sms-send -n "{args.sms}" "An External Object Detected"', True, PIPE, PIPE, "Can't send message to you")
            print(f"\nMessage sent to {args.sms}")

        if args.call is not None:
            print(f"\nCalling {args.call}\n")
            processCommand(f"termux-telephony-call {args.call}", True, DEVNULL, STDOUT, "Can't call you")

        event_folder = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        os.makedirs(event_folder, exist_ok=True)
        audio_path = os.path.join(event_folder, f"{event_folder}.aac")

        processCommand(f'termux-microphone-record -f "{audio_path}" -e aac', True, DEVNULL, STDOUT, "Can't record audio")
        print("\nIt's taking Photos...")

        start = time()
        for i in range(args.number):
            now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            photo_path = os.path.join(event_folder, f"{now}_{i+1:03d}.jpeg")
            print(f"Capturing photo {i+1}/{args.number}...", end="\r")
            processCommand(f'termux-camera-photo -c {args.camera} "{photo_path}"', True, PIPE, PIPE, "Can't take photo")
        end = time()

        delTime = max(1.0, end - start)
        fr = max(1.0, round(args.number / delTime, 1))

        processCommand("termux-microphone-record -q", True, DEVNULL, STDOUT, "Can't stop audio recorder!")

        if args.video == 1:
            print('\nConverting photos to video...')
            video_path = os.path.join(event_folder, f"{event_folder}.mp4")
            ffmpeg_cmd = (
                f"ffmpeg -y -framerate {fr} -pattern_type glob -i '{event_folder}/*.jpeg' "
                f"-i '{audio_path}' -c:v libx264 -tune stillimage -c:a aac -b:a 192k "
                f"-pix_fmt yuv420p -shortest '{video_path}'"
            )
            processCommand(ffmpeg_cmd, True, PIPE, PIPE, "Can't convert to video")
            print(f"\nVideo file created at: {video_path}\n")

        if args.chatid is not None and args.bottoken is not None:
            msg_cmd = (
                f'curl -s -X POST -F chat_id="{args.chatid}" '
                f'-F text="An external object detected, here are the photos:" '
                f'https://api.telegram.org/bot{args.bottoken}/sendMessage'
            )
            processCommand(msg_cmd, True, DEVNULL, STDOUT, "Can't send Text to telegram!")
            print('\nStart sending taken photos to telegram:\n')

            photos = [x for x in sorted(os.listdir(event_folder)) if x.endswith(".jpeg")]
            for idx, x in enumerate(photos, 1):
                send_photo_to_telegram(args.chatid, args.bottoken, event_folder, x)
                print(f"Uploading photos to telegram: {idx} of {len(photos)}", end="\r")
            print("\nAll your photos sent to your telegram bot\n")

        countdown(3)
        print('\nAgain Setting up the device..')
        delta, last_img = setup_num(args.camera)
        if last_img is not None:
            imgArr = [last_img]
