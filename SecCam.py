
from PIL import Image, ImageChops
import numpy as np
from os import listdir
from datetime import datetime
from argparse import ArgumentParser
from time import sleep
from subprocess import Popen, PIPE, DEVNULL, STDOUT, run


def dif_of_images(image1, image2):
    return np.sum(np.array(ImageChops.difference(image1, image2).getdata()))

def setup_num():
	try:
		process = Popen(["termux-camera-photo","-c", args.camera, "001.jpeg"], stdout=PIPE, stderr=PIPE)
		stdout, stderr = process.communicate()
	except Exception:
		print(stderr)
		pass
	sleep(1)
	try:
		process = Popen(["termux-camera-photo","-c", args.camera, "002.jpeg"], stdout=PIPE, stderr=PIPE)
		stdout, stderr = process.communicate()
	except Exception:
		print(stderr)
		pass
	img1 = Image.open('001.jpeg')
	img2 = Image.open('002.jpeg')
	img1=img1.resize((872,1160))
	img2=img2.resize((872,1160))
	delta1=dif_of_images(img1, img2)
	try:
		process = Popen(["termux-camera-photo","-c", args.camera, "003.jpeg"], stdout=PIPE, stderr=PIPE)
		stdout, stderr = process.communicate()
	except Exception:
		print(stderr)
		pass
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
	try:
		process = Popen("curl -X POST -H \"Content-Type:multipart/form-data\" -F chat_id="+Chat_ID+" -F photo=@\""+path+"/"+image+"\" https://api.telegram.org/bot"+Bot_Token+"/sendPhoto",shell=True, stdout=DEVNULL, stderr=STDOUT)
		stdout, stderr = process.communicate()
	except Exception:
		print('Something went wrong!')
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
	try:
		process=Popen("termux-camera-photo -c " + args.camera + " 00" + str(count) + ".jpeg", shell=True, stdout=PIPE, stderr=PIPE)
		stdout, stderr = process.communicate()
	except Exception:
		print(stderr)
		pass
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
			try:
				process = Popen("termux-sms-send -n"+ args.sms+"An External Object Detected", shell=True, stdout=PIPE, stderr=PIPE)
				stdout, stderr = process.communicate()
			except Exception:
				print('Can\'t send message to you')
				pass
			print(f"\nmessage sent to {args.sms}")
		if args.call is not None:
			print(f"\nCalling {args.call}\n")
			try:
				process=Popen("termux-telephony-call "+args.call, shell=True, stdout=DEVNULL, stderr=STDOUT)
				stdout, stderr = process.communicate()
			except Exception:
				print('Can\'t call you!')
				pass
		print("\nIt's taking Photos")
		for i in range(args.number):
			now=datetime.now().strftime("%Y-%m-%d_%H%M%S")
			try:
				process=Popen("termux-camera-photo -c " + args.camera + " "+now+".jpeg", shell=True, stdout=PIPE, stderr=PIPE)
				stdout, stderr = process.communicate()
			except Exception:
				print(stderr)
				pass
		try:
			process=Popen("mkdir " + now, shell=True, stdout=DEVNULL, stderr=STDOUT)
			stdout, stderr = process.communicate()
		except Exception:
			print('Can\'t make folder!')
			pass
		try:
			process=Popen("mv " + now[:2] + "*.jpeg " + now + "/", shell=True, stdout=DEVNULL, stderr=STDOUT)
			stdout, stderr = process.communicate()
		except Exception:
			print('Something went wrong!')
			pass
		if args.video==1:
			print('\nConverting photos to video..')
			try:
				process=Popen("ffmpeg -framerate 3 -pattern_type glob -i '" + now + "/*.jpeg' -c:v libx264 -pix_fmt yuv420p " + now + "/" + now + ".mp4 > /dev/null 2>&1", shell=True, stdout=DEVNULL, stderr=STDOUT)
				stdout, stderr = process.communicate()
			except Exception:
				print('Can\'t convert photos to video file!')
				pass
			print(f"\nVideo file {now}.mp4 created in \n{now}/ folder.\n")
		if args.chatid is not None and args.bottoken is not None:
			try:
				process=Popen("curl -X POST -H \"Content-Type:multipart/form-data\" -F chat_id="+args.chatid+" -F text=\"An external object detected, here are the photos:\" https://api.telegram.org/bot"+args.bottoken+"/sendMessage > /dev/null 2>&1", shell=True, stdout=DEVNULL, stderr=STDOUT)
				stdout, stderr = process.communicate()
			except Exception:
				print('Can\'t send Text to telegram!')
				pass
			print('\nStart to send taken photos to telegram:\n')
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
