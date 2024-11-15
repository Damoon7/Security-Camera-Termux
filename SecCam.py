from PIL import Image, ImageChops
import numpy as np
from os import system
from datetime import datetime
import os
from argparse import ArgumentParser
from time import sleep

def dif_of_images(image1, image2):
    return np.sum(np.array(ImageChops.difference(image1, image2).getdata()))

def setup_num():
	system("termux-camera-photo -c " + args.camera + " 001.jpeg")
	sleep(1)
	system("termux-camera-photo -c " + args.camera + " 002.jpeg")
	img1 = Image.open('001.jpeg')
	img2 = Image.open('002.jpeg')
	img1=img1.resize((872,1160))
	img2=img2.resize((872,1160))
	delta1=dif_of_images(img1, img2)
	system("termux-camera-photo -c "+args.camera+" 003.jpeg")
	img3 = Image.open('003.jpeg')
	img3=img3.resize((872,1160))
	delta2=dif_of_images(img2, img3)
	print('Device is set up')
	if delta2>delta1:
		delta=delta2*1.25-delta1/4
	else:
		delta=delta1*1.25-delta2/4
	return delta

def send_photo_to_telegram(Chat_ID, Bot_Token,path, image):
        system("curl -X POST -H \"Content-Type:multipart/form-data\" -F chat_id="+Chat_ID+" -F photo=@\""+path+"/"+image+"\" https://api.telegram.org/bot"+Bot_Token+"/sendPhoto > /dev/null 2>&1")



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


system("clear")


parser = ArgumentParser()
parser.add_argument('-C','--camera', help='0 for back Camera, 1 for front Camera', type=str, default=0)
parser.add_argument('-v','-V','--video', help='1 to convert photos to video file and 0 for dont convert', type=int, default=0)
parser.add_argument('-sms', '--sms', help='Enter phone number to send sms (by this format : +98...)')
parser.add_argument('-c','--call', help='Enter phone number to call (by this format : +98...)')
parser.add_argument('-n','--number', help='number of photos you want to be captured', type=int, default=50)
parser.add_argument('-s','--seconds', help='Seconds needs to set the device', type=int, default=30)
parser.add_argument('-ch','--chatid', help='your chat id', type=str)
parser.add_argument('-bt','--bottoken', help='your telegram bot token', type=str)
args = parser.parse_args()



# to wake up termux while phone is locked:
system("termux-wake-lock")

system("clear")

countdown(args.seconds)

print('Setting up the device...\n')
delta=setup_num()

count=1

while True:
	if count>3:
		count-=2
	system("termux-camera-photo -c " + args.camera + " 00" + str(count) + ".jpeg")
	count=count+1
	img1 = Image.open('001.jpeg')
	img2 = Image.open('002.jpeg')
	img1=img1.resize((872,1160))
	img2=img2.resize((872,1160))
	dif=dif_of_images(img1, img2)
	clock()
	if dif>delta:
		print('\nAn External Object Detected')
		if args.sms is not None:
			system("termux-sms-send -n " + args.sms + " An External Object Detected")
			print("\nmessage sent to your phone")
		if args.call is not None:
			system("termux-telephony-call "+args.call)
		print("\nIt's taking Photos")
		for i in range(args.number):
			now=datetime.now().strftime("%Y-%m-%d_%H%M%S")
			system("termux-camera-photo -c " + args.camera + " "+now+".jpeg")
		system("mkdir " + now)
		system("mv " + now[:2] + "*.jpeg " + now + "/")
		if args.video==1:
			print('\nConverting photos to video..')
			system("ffmpeg -framerate 3 -pattern_type glob -i '" + now + "/*.jpeg' -c:v libx264 -pix_fmt yuv420p " + now + "/" + now + ".mp4 > /dev/null 2>&1")
			print("\nVideo file " + now + ".mp4 created in " + now + "/ folder.")
		if args.chatid is not None and args.bottoken is not None:
			i=1
			for x in os.listdir(now+"/"):
				if x.endswith(".jpeg"):
					send_photo_to_telegram(args.chatid, args.bottoken, now, x)
					st="uploading photos to telegram : "+str(i)+" of "+str(args.number)
					print(st,end="\r")
					i+=1
			print("\nAll your photos sent to your telegram bot\n")
		countdown(3)
		print('\nAgain Setting up the device..')
		delta=setup_num()
