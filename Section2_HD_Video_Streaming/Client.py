from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os, time
from tkinter import StringVar
import math

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"


class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    # ---------------------------------------------------------
    # Init
    # ---------------------------------------------------------
    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename

        # Stats
        self.totalBytes = 0
        self.totalPackets = 0
        self.lossPackets = 0
        self.startTime = 0
        self.frameNbr = 0
        self.excepted_seq_num = 0

        self.rtspSeq = 0
        self.sessionId = 0
        self.teardownAcked = 0
        self.requestSent = -1
        self.playEvent = threading.Event()
        
        self.prev_arrival = None
        self.prev_timestamp = None
        self.jitter = 0.0

        self.master.geometry("1000x700")
        self.master.minsize(800, 600)
        self.master.resizable(True, True)

        self.createWidgets()
        self.connectToServer()

    # ---------------------------------------------------------
    # GUI
    # ---------------------------------------------------------
    def createWidgets(self):
        
        self.FRAME_WIDTH = 1280
        self.FRAME_HEIGHT = 720
        
        # Placeholder image
        blank = Image.new("RGB", (self.FRAME_WIDTH, self.FRAME_HEIGHT), (0, 0, 0))
        self.blank_frame = ImageTk.PhotoImage(blank)

		# -------------------------------
		# VIDEO DISPLAY (only 1 label!)
		# -------------------------------
        self.videoLabel = Label(
			self.master,
			image=self.blank_frame,
			width=self.FRAME_WIDTH,
			height=self.FRAME_HEIGHT
		)
        self.videoLabel.grid(row=0, column=0, columnspan=4, padx=10, pady=10)

		# -------------------------------
		# CONTROL BUTTONS
		# -------------------------------
        self.setupBtn = Button(self.master, width=20, text="Setup", command=self.setupMovie)
        self.setupBtn.grid(row=1, column=0, pady=5)

        self.playBtn = Button(self.master, width=20, text="Play", command=self.playMovie)
        self.playBtn.grid(row=1, column=1, pady=5)

        self.pauseBtn = Button(self.master, width=20, text="Pause", command=self.pauseMovie)
        self.pauseBtn.grid(row=1, column=2, pady=5)

        self.teardownBtn = Button(self.master, width=20, text="Teardown", command=self.exitClient)
        self.teardownBtn.grid(row=1, column=3, pady=5)

		# -------------------------------
		# NETWORK STATISTICS PANEL
		# -------------------------------
        self.statsFrame = Frame(self.master, bd=2, relief="groove", padx=10, pady=5)
        self.statsFrame.grid(row=2, column=0, columnspan=4, sticky="we", padx=10, pady=10)
        
        Label(self.statsFrame, text="Network Statistics", font=("Arial", 12)).grid(
			row=0, column=0, columnspan=1, pady=3
		)

        self.bandwidthVar = StringVar(value="Bandwidth: 0 Mbps")
        self.framerateVar = StringVar(value="Frame Rate: 0 fps")
        self.packetlossVar = StringVar(value="Packet Loss: 0% (0 packets)")
        self.framesVar = StringVar(value="Frames: 0")
        self.packetsVar = StringVar(value="Packets: 0")
        self.jitterVar = StringVar(value="Jitter: 0 ms")

        Label(self.statsFrame, textvariable=self.bandwidthVar, anchor="w").grid(row=1, column=0, sticky="w")
        Label(self.statsFrame, textvariable=self.framerateVar, anchor="w").grid(row=1, column=1, sticky="w")
        Label(self.statsFrame, textvariable=self.packetlossVar, anchor="w").grid(row=2, column=0, sticky="w")
        Label(self.statsFrame, textvariable=self.framesVar, anchor="w").grid(row=2, column=1, sticky="w")
        Label(self.statsFrame, textvariable=self.packetsVar, anchor="w").grid(row=3, column=0, sticky="w")
        Label(self.statsFrame, textvariable=self.jitterVar, anchor="w").grid(row=3, column=1, sticky="w")
        
        self.master.grid_rowconfigure(0, weight=1)  
        self.master.grid_rowconfigure(1, weight=0)   
        self.master.grid_rowconfigure(2, weight=0)   
        
        self.master.grid_columnconfigure(0, weight=1)
        self.master.grid_columnconfigure(1, weight=1)
        self.master.grid_columnconfigure(2, weight=1)
        self.master.grid_columnconfigure(3, weight=1)

    # ---------------------------------------------------------
    # PLAY / PAUSE / SETUP
    # ---------------------------------------------------------
    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def playMovie(self):
        if self.state == self.READY:
            threading.Thread(target=self.listenRtp).start()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)
            # Only reset stats on first play, not when resuming from pause
            if self.frameNbr == 0:
                self.totalBytes = 0
                self.totalPackets = 0
                self.lossPackets = 0
                self.excepted_seq_num = 0
                self.startTime = time.time()

    def pauseMovie(self):
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def exitClient(self):
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()
        try:
            os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except:
            pass

    # ---------------------------------------------------------
    # RTP Listening
    # ---------------------------------------------------------
    def listenRtp(self):
        temp_buf = bytearray()
        timeout_count = 0
        max_timeouts = 5  # If no data for 5 consecutive timeouts, stream ended
        synced = False  # Track if we've synchronized to frame boundaries

        while True:
            try:
                data = self.rtpSocket.recv(20480)
                if data:
                    timeout_count = 0  # Reset timeout counter on successful receive
                    rtp = RtpPacket()
                    rtp.decode(data)
                    curr_seq = rtp.seqNum()
                    # Jitter calculation
                    arrival = time.time()
                    rtp_timestamp = rtp.timestamp()

                    if self.prev_arrival is not None:
                        D = (arrival - self.prev_arrival) - ((rtp_timestamp - self.prev_timestamp) / 1000)
                        self.jitter += (abs(D) - self.jitter) / 16

                    self.prev_arrival = arrival
                    self.prev_timestamp = rtp_timestamp

                    # Detect packet loss
                    if self.excepted_seq_num != 0 and curr_seq > self.excepted_seq_num:
                        lost = curr_seq - self.excepted_seq_num
                        self.lossPackets += lost
                        print(f"[PACKET LOSS] Lost {lost} packets ({self.excepted_seq_num} → {curr_seq-1})")

                    self.excepted_seq_num = curr_seq + 1

                    self.totalBytes += len(data)
                    self.totalPackets += 1

                    # If not synced yet, wait for end of frame marker to sync
                    if not synced:
                        if rtp.getMarker() == 1:
                            synced = True
                            temp_buf = bytearray()  # Start fresh after sync
                        continue  # Skip until we're synced
                    
                    temp_buf += rtp.getPayload()

                    # Check marker bit to see if frame is complete
                    if rtp.getMarker() == 1:
                        framesize = len(temp_buf)
                        packet_count = math.ceil(framesize / 1400)
                        print(f"Frame {self.frameNbr + 1}: {framesize} bytes needs {packet_count} packets")

                        self.frameNbr += 1
                        self.updateMovie(self.writeFrame(temp_buf))
                        temp_buf = bytearray()
            except socket.timeout:
                if self.playEvent.isSet():
                    break
                # Socket timeout - check if stream has ended
                timeout_count += 1
                if timeout_count >= max_timeouts:
                    print("[STREAM END] No more data received, stream ended.")
                    self.state = self.READY  # Stop stats updates
                    break
            except:
                if self.playEvent.isSet():
                    break
                if self.teardownAcked == 1:
                    self.rtpSocket.close()
                    break

    # ---------------------------------------------------------
    # Frame writing & updating
    # ---------------------------------------------------------
    def writeFrame(self, data):
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        with open(cachename, "wb") as f:
            f.write(data)
        return cachename

    def updateMovie(self, imageFile):
        try:
            img = Image.open(imageFile)
            w = self.videoLabel.winfo_width()
            h = self.videoLabel.winfo_height()
            if w > 10 and h > 10: 
                img = img.resize((w, h))

            photo = ImageTk.PhotoImage(img)
            self.videoLabel.configure(image=photo)
            self.videoLabel.image = photo
        except:
            print("Broken image frame")
    # ---------------------------------------------------------
    # Stats Panel Auto Update
    # ---------------------------------------------------------
    def updateStatsPanel(self):
        now = time.time()
        duration = max(now - self.startTime, 0.00001)

        # Bandwidth
        bandwidth = (self.totalBytes * 8) / (duration * 1_000_000)
        self.bandwidthVar.set(f"Bandwidth: {bandwidth:.2f} Mbps")

        # Packet Loss %
        total_expected = self.totalPackets + self.lossPackets
        lossRate = (self.lossPackets / total_expected) * 100 if total_expected > 0 else 0
        self.packetlossVar.set(f"Packet Loss: {lossRate:.1f}% ({self.lossPackets} packets)")

        # Frames count
        self.framesVar.set(f"Frames: {self.frameNbr}")

        # Packets count
        self.packetsVar.set(f"Packets: {self.totalPackets}")

        # Frame Rate
        fr = self.frameNbr / duration
        self.framerateVar.set(f"Frame Rate: {fr:.1f} fps")

        # Jitter display
        jitter_ms = self.jitter * 1000
        self.jitterVar.set(f"Jitter: {jitter_ms:.2f} ms")

        # Refresh every 0.5 sec
        if self.state == self.PLAYING:
            self.master.after(500, self.updateStatsPanel)

    # ---------------------------------------------------------
    # RTSP
    # ---------------------------------------------------------
    def connectToServer(self):
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning('Connection Failed', 'Cannot connect to server.')

    def sendRtspRequest(self, code):

        if code == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()

            self.rtspSeq += 1
            request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port= {self.rtpPort}"
            self.requestSent = self.SETUP

        elif code == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PLAY

        elif code == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PAUSE

        elif code == self.TEARDOWN and self.state != self.INIT:
            self.rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.TEARDOWN
        else:
            return

        self.rtspSocket.send(request.encode())
        print("\nData sent:\n" + request)

    def recvRtspReply(self):
        while True:
            reply = self.rtspSocket.recv(1024)
            if reply:
                self.parseRtspReply(reply.decode())

            if self.requestSent == self.TEARDOWN:
                self.rtspSocket.close()
                break

    def parseRtspReply(self, data):
        lines = data.split('\n')
        seqNum = int(lines[1].split(' ')[1])

        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])

            if self.sessionId == 0:
                self.sessionId = session

            if self.sessionId == session:
                code = int(lines[0].split(' ')[1])

                if code == 200:
                    if self.requestSent == self.SETUP:
                        self.state = self.READY
                        self.openRtpPort()

                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING
                        # Start stats panel updates now that we're playing
                        self.updateStatsPanel()

                    elif self.requestSent == self.PAUSE:
                        self.playEvent.set()
                        self.state = self.READY

                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                        self.teardownAcked = 1

    def openRtpPort(self):
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        try:
            self.rtpSocket.bind(("", self.rtpPort))
        except:
            tkMessageBox.showwarning("Error", "Unable to bind RTP port.")

    # ---------------------------------------------------------
    def handler(self):
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit", "Are you sure?"):
            self.exitClient()
        else:
            self.playMovie()
