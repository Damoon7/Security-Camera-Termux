#!/usr/bin/python
from PIL import Image, ImageChops
import numpy as np
from os import listdir, remove
from datetime import datetime
from argparse import ArgumentParser
from time import sleep, time
from subprocess import Popen, PIPE, DEVNULL, STDOUT, run
from statistics import stdev, mean
from random import randint


def dif_of_images(image1, image2):
    return np.sum(np.array(ImageChops.difference(image1, image2).getdata()))*10**-3


def setup_num():
	std=1
	avg=0
	while std > avg * 0.2:
		imgArray = []
		delta = []
		maxdel = 0
		while len(imgArray) < 15 or maxdel/(avg+std) < 1.25 :
			for i in range(0,5) :
				processCommand("termux-camera-photo -c " + str(args.camera) + " img_set.jpeg",True,PIPE,PIPE,"Can\'t take photo")
				img = Image.open('img_set.jpeg')
				img=img.resize((225,300)).convert('L')
				imgArray.append(img)
				sleep(randint(1,5))
			for x in range(0,len(imgArray)):
				for y in range(x+1,len(imgArray)):
					delta.append(dif_of_images(imgArray[x], imgArray[y]))
			maxdel = max(delta)
			std = stdev(delta)
			avg = mean(delta)
			if len(imgArray)>=25:
				break
		remove("img_set.jpeg")
		imgArray.clear()
		delta.clear()
	print('Device is set up')
	return maxdel + avg + std


def send_photo_to_telegram(Chat_ID, Bot_Token,path, image):
	processCommand("curl -X POST -H \"Content-Type:multipart/form-data\" -F chat_id="+Chat_ID+" -F photo=@\""+path+"/"+image+"\" https://api.telegram.org/bot"+Bot_Token+"/sendPhoto",True,DEVNULL, STDOUT, "Can\'t send to telegram")



def processCommand(command, shellVal, stout ,sterr, err):
	if shellVal:
		try:
			process = Popen(command, shell=shellVal, stdout=stout, stderr=sterr)
			stdout, stderr = process.communicate()
		except Exception:
			print(err)
			pass
	else:
		try:
			process = Popen(command, stdout=stout, stderr=sterr)
			stdout, stderr = process.communicate()
		except Exception:
			print(err)
			pass


def countdown(t):
	while t:
		mins, secs = divmod(t, 60)
		timer = '{:02d}:{:02d}'.format(mins, secs)
		print(timer, end="\r")
		sleep(1)
		t -= 1


def clock():
	cl=datetime.now().strftime("%H:%M:%S")
	print(cl, end="\r")


# Arguments:
parser = ArgumentParser()
parser.add_argument('-C','--camera', help='0 for back Camera, 1 for front Camera', type=str, default=0)
parser.add_argument('-v','-V','--video', help='1 to convert photos to video file and 0 for dont convert', type=int, default=0)
parser.add_argument('-sms', '--sms', help='Enter phone number to send sms')
parser.add_argument('-c','--call', help='Enter phone number to call')
parser.add_argument('-n','--number', help='number of photos you want to be captured', type=int, default=50)
parser.add_argument('-s','--seconds', help='Seconds needs to set the device', type=int, default=30)
parser.add_argument('-ch','--chatid', help='your chat id', type=str)
parser.add_argument('-bt','--bottoken', help='your telegram bot token', type=str)
args = parser.parse_args()



#   ***  Main Code:   ***

process=run("clear", shell=True) # Clear the screen
process=run("termux-wake-lock", shell=True) # to wake up Termux while phone is locked
countdown(args.seconds)
print('Setting up the device...\n')
delta=setup_num()

imgArr=[]
for k in range(0,3):
	processCommand("termux-camera-photo -c " + str(args.camera) + " photo.jpeg",True, PIPE, PIPE, "Can\'t take photo")
	img = Image.open('photo.jpeg')
	img=img.resize((225,300)).convert('L')
	imgArr.append(img)
	remove("photo.jpeg")
	sleep(2)

while True:
	clock()
	dif = []
	processCommand("termux-camera-photo -c " + str(args.camera) + " photo.jpeg",True, PIPE, PIPE, "Can\'t take photo")
	img = Image.open('photo.jpeg')
	img=img.resize((225,300)).convert('L')
	imgArr.append(img)
	remove("photo.jpeg")
	for x in range(0,len(imgArr)):
		for y in range(x+1,len(imgArr)):
			dif.append(dif_of_images(imgArr[x], imgArr[y]))
	imgArr.pop(0)
	isLarge=[]
	for z in dif:
		isLarge.append(z>delta)
	if all(isLarge) :
		print('\nAn External Object Detected')
		if args.sms is not None:
			processCommand("termux-sms-send -n"+ args.sms+"An External Object Detected",True, PIPE, PIPE, "Can\'t send message to you")
			print(f"\nmessage sent to {args.sms}")
		if args.call is not None:
			print(f"\nCalling {args.call}\n")
			processCommand("termux-telephony-call " + args.call,True, DEVNULL, STDOUT, "Can\'t call you")
		audioName=datetime.now().strftime("%Y-%m-%d_%H%M%S")
		processCommand("termux-microphone-record -f " + audioName + ".aac -e aac",True,DEVNULL, STDOUT, "Can\'t record audio")
		print("\nIt's taking Photos")
		start=time()
		for i in range(args.number):
			now=datetime.now().strftime("%Y-%m-%d_%H%M%S")
			processCommand("termux-camera-photo -c " + str(args.camera) + " "+now+".jpeg",True, PIPE, PIPE, "Can\'t take photo")
		end=time()
		delTime=end-start
		fr=round(0.3*delTime/args.number,1)
		processCommand("termux-microphone-record -q",True,DEVNULL, STDOUT, "Can\'t stop audio recorder!")
		processCommand("mkdir " + now,True,DEVNULL, STDOUT, "Can\'t make the folder")
		processCommand("mv " + now[:2] + "*.jpeg " + now + "/",True, DEVNULL, STDOUT, "Can\'t move files to target folder")
		if args.video==1:
			print('\nConverting photos to video..')
			processCommand("ffmpeg -framerate " + str(fr) + " -pattern_type glob -i '" + now + "/*.jpeg' -i " + audioName + ".aac -c:v libx264 -tune stillimage -c:a aac -b:a 192k -pix_fmt yuv420p " + now + ".mp4",True, PIPE, PIPE, "Can\'t convert to video")
			processCommand("mv "+ now + ".mp4 " + now + "/",True, DEVNULL, STDOUT, "Can\'t move video file  to target folder")
			print(f"\nVideo file {now}.mp4 created in \n{now}/ folder.\n")
		processCommand("mv "+ audioName + ".aac " + now + "/",True, DEVNULL, STDOUT, "Can\'t move audio file to target folder")
		if args.chatid is not None and args.bottoken is not None:
			processCommand("curl -X POST -H \"Content-Type:multipart/form-data\" -F chat_id="+args.chatid+" -F text=\"An external object detected, here are the photos:\" https://api.telegram.org/bot"+args.bottoken+"/sendMessage",True, DEVNULL, STDOUT, "Can\'t send Text to telegram!")
			print('\nStart sending taken photos to telegram:\n')
			i=1
			for x in listdir(now+"/"):
				if x.endswith(".jpeg"):
					send_photo_to_telegram(args.chatid, args.bottoken, now, x)
					st="uploading photos to telegram : "+str(i)+" of "+str(args.number)
					print(st,end="\r")
					i+=1
			print("\nAll your photos sent to your telegram bot\n")
		countdown(5)
		print('\nAgain Setting up the device..')
		delta=setup_num()
	sleep(2)