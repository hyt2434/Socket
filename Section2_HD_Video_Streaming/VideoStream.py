import sys
class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0
		self.buffer = bytearray()
		
	def nextFrame(self):
		"""Get next frame."""
		data = bytearray()
        # find header of JPEG frame(0xFF 0xD8)
		while True:
			# If buffer is empty or less than 2 bytes, read from file
			if (len(self.buffer) < 2): 
				chunk = self.file.read(4096) # Read in 4KB chunks
				if not chunk:
					return None  # End of file
				self.buffer.extend(chunk)

			# Find start code (FF D8)
			start_index = self.buffer.find(b'\xff\xd8')
			if start_index != -1:
				self.buffer = self.buffer[start_index:]  # Discard data before start code
				break
			else:
				keep_byte = self.buffer[-1:]  # Keep last byte in case start code spans chunks
				self.buffer = keep_byte
		
		# find end of JPEG frame(0xFF 0xD9)
		while True:
			end_index = self.buffer.find(b'\xff\xd9',2)
			if end_index != -1:
				frame_data = self.buffer[:end_index + 2]  # Include the end code
				self.buffer = self.buffer[end_index + 2:]
				self.frameNum += 1
				return bytes(frame_data)
			else:
				# Read more data from file
				chunk = self.file.read(4096) # Read in 4KB chunks
				if not chunk:
					if len(self.buffer) > 0:
						frame = self.buffer
						self.buffer = bytearray()
						return bytes(frame)
					return None
				self.buffer.extend(chunk)		
	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum     
	
	