from PIL import Image, ImageChops
import numpy as np
from os import listdir
from datetime import datetime
from argparse import ArgumentParser
from time import sleep, time, ctime
from subprocess import Popen, PIPE, DEVNULL, STDOUT, run


def dif_of_images(image1, image2):
    return np.sum(np.array(ImageChops.difference(image1, image2).getdata()))

def setup_num():
	processCommand("termux-camera-photo -c " + args.camera + " 001.jpeg",True,PIPE,PIPE,"Can\'t take photo")
	sleep(1)
	processCommand("termux-camera-photo -c " + args.camera + " 002.jpeg",True,PIPE,PIPE,"Can\'t take photo")
	img1 = Image.open('001.jpeg')
	img2 = Image.open('002.jpeg')
	img1=img1.resize((872,1160))
	img2=img2.resize((872,1160))
	delta1=dif_of_images(img1, img2)
	processCommand("termux-camera-photo -c " + args.camera + " 003.jpeg",True,PIPE,PIPE, "Can\'t take photo")
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

process=run("clear", shell=True)


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



# to wake up Termux while phone is locked:
process=run("termux-wake-lock", shell=True)

countdown(args.seconds)

print('Setting up the device...\n')

delta=setup_num()

count=1
while True:
	if count>3:
		count-=2
	processCommand("termux-camera-photo -c " + args.camera + " 00" + str(count) + ".jpeg",True, PIPE, PIPE, "Can\'t take photo")
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
			processCommand("termux-camera-photo -c " + args.camera + " "+now+".jpeg",True, PIPE, PIPE, "Can\'t take photo")
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
		countdown(3)
		print('\nAgain Setting up the device..')
		delta=setup_num()
