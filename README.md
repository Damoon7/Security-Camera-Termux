# Security Camera by Termux

This is a python script that by running it on Termux app in an Android phone you can use your phone as a security camera.

It regularly takes pictures of the desired place and if an external object enters, it starts to take pictures over and over and save them to phone memory.

It also can do these:
- Send message to your phone when detects an external object.
- Convert all taken pictures to a video file.
- Call you, to alarm you
- Send all taken pictures to your telegram

*Tip: All token pictures and the converted video save in a separate folder named the current date and time.



## Installation

At first you should install [Termux](https://github.com/termux/termux-app) app from [Google Play](https://play.google.com/store/apps/details?id=com.termux), [FDroid](https://f-droid.org/packages/com.termux/) or [Github](https://github.com/termux/termux-app/releases). I suggest you to install it from github or FDroid.

After you must install requirements. at first run these:

```bash
apt update && apt upgrade -y
```

then you should install  python and some packages on termux that you need to run this script:


```bash
apt install python make wget termux-exec clang libjpeg-turbo freetype -y
```
and install Pillow package:

```bash
env INCLUDE="$PREFIX/include" LDFLAGS=" -lm" pip install Pillow
```

at last install numpy package:

```bash
pkg install python-numpy
```



## Execution

for executing this script you can run this command that uses default options:

```bash
python3 SecCam.py
```

help command is:

```bash
python3 SecCam.py -h
```

you have some options to run this code:

1. -C or --camera : 0 for back Camera, 1 for front Camera, default is 0.
2. -v, -V or --video : 1 to convert photos to video file and 0 to dont convert, (default is 0)
3. -sms or --sms Your_Phone_Number (replace Your_Phone_Number by your phone number with zip code like: +98...)
4. -c or --call Your_Phone_Number (replace Your_Phone_Number by your phone number with zip code like: +98...)
5. -n or --number 50 : number of photos you want to be captured (replace 50 by what number you want, default is 50)
6. -s or --seconds 30 : Seconds you need to set the device (default is 30)
7. -ch --chatid Chat_ID : replace Chat_ID by your telegram chat ID
8. -bt or --bottoken  Bot_Token : replace Bot_Token by your telegram bot token

** Notice: To send photos to telegram you have to enter both chat ID and bot token.

Here is an example for you:

```bash
python3 SecCam.py -C 1 -v 1 -sms Your_Phone_Number -c Your_Phone_Number -n 200 -s 180 -ch Chat_ID -bt Bot_Token
```
Now enjoy it 😉
