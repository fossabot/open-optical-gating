#Python imports
import numpy as np
import io
import picamera
from picamera import array
import time
import os
import sys
import serial
from skimage import io
import matplotlib.pyplot as plt
from tqdm import tqdm
import shutil
import json
from datetime import datetime

# Local imports
import j_py_sad_correlation as jps
import fastpins as fp
import getPeriod as gp
import realTimeSync as rts
import helper as hlp
import stage_control_functions as scf

class YUVLumaAnalysis(array.PiYUVAnalysis):

	#Custom class to convert and analyse Y (luma) channel of each YUV frame.
	#Extends the picamera.array.PiYUVAnalysis class, which has a stub method called analze that is overidden here.


	def __init__(self, camera=None, usb_serial=None, brightfield_framerate=80, laser_trigger_pin=22 , fluorescence_camera_pins=(8,10,12), plane_address=1, encoding='utf-8', terminator=chr(13)+chr(10), increment=0.0005, negative_limit=0, positive_limit=0.075, current_position=0, frame_buffer_length=100, ref_frames = None, frame_num = 0,  live=True, output_mode='glaSPIM',log=False):


		# Function inputs:
		#	camera = the raspberry picam PiCamera object
		#	laser_trigger_pin = the pin number (int) of the laser trigger
		#	fluorescence_camera_pins = an array (int) of fluorescence camera pin numbers containg (trigger,SYNC-A, SYNC-B)
		#	usb_serial = the usb serial object for controlling the movement stages
		#	plane_address = the address of the stage that moves the zebrafish through the light sheet
		#	encoding = the encoding used to control the Newport stages (usually utf-8)
		#	terminator = the character set used to terminate a command sent to the Newport stages
		#	increment = the required increment to move the stage by after each image capture (float)
		#	negative_limit = the smallest z value (float) of the edge of the zebrafish heart (selected by the user)
		#	positive_limit = the largest z value (float) of the edge of the zebrafish heart (selected by the user)
		#	current_position = the current z value of the stage.

		# Optional inputs:
		#	ref_frames = a set of reference frames containg a whole period for the zebrafish
		# 	frame_num = the current frame number



		super(YUVLumaAnalysis, self).__init__(camera)
		self.frame_num = frame_num
		if camera is not None:
			self.width, self.height = camera.resolution
		else:
			self.width, self.height = (128,128)

		self.framerate = brightfield_framerate

		# Defines laser, fluorescence camera and usb serial information
		self.laser_trigger_pin = laser_trigger_pin
		self.fluorescence_camera_pins = fluorescence_camera_pins

		self.usb_serial = usb_serial
		self.plane_address = plane_address
		self.encoding = encoding
		self.terminator = terminator
		self.increment = increment
		self.negative_limit = negative_limit
		self.positive_limit = positive_limit
		self.current_position = current_position

		self.camera = camera

		# Defines the arrays for sad and frameSummaryHistory (which contains period, timestamp and argmin(sad))
		self.frame_buffer_length = frame_buffer_length
		self.frameSummaryHistory = np.zeros((self.frame_buffer_length,3))
		self.dtype = 'uint8'

		# Variable for emulator
		self.live = live 
		self.targetSyncPhaseOld = -1

		# Array for fps test
		self.time_ary = []


		# Sets ouput mode, reverts to Glasgow SPIM mode by default (mode specified in JSON file)
		if output_mode == '5V_BNC_Only':
			self.outputMode = 1
		else:
			self.outputMode = 0	

		# Sets the log status
		self.log = log

		# Initialises reference frames if not specified
		if ref_frames is None:

			self.ref_frames = np.empty((self.frame_buffer_length, self.height, self.width), dtype=self.dtype)
			self.get_period_status = 2

		else:
			self.ref_frames = ref_frames
			self.get_period_status = 0
			self.settings = hlp.initialiseSettings(framerate=self.framerate, referencePeriod=ref_frames.shape[0])
			self.settings = rts.deduceBarrierFrameArray(self.settings)
			self.initial_process_time = time.time()
		


	def analyze(self, frame):
		# For logging processing time
		time_init = time.time()
		if len(frame.shape) == 3:
			frame = frame[:,:,0]
		#method to analyse each frame as they are captured by the camera. Must be fast since it is running within
		#the encoder's callback, and so must return before the next frame is produced.

		# Ensures stage is always within user defined limits
		if self.current_position <= self.positive_limit and self.current_position >= self.negative_limit:
			# Captures a set of reference frames for obtaining a reference period
			if self.get_period_status == 2:

				# Obtains a minimum amount of reference frames
				if self.frame_num < self.frame_buffer_length:

					# Adds current frame to reference
					self.ref_frames[self.frame_num,:,:] = frame
					
					# Increases frame number
					self.frame_num += 1

				# Once a suitible reference size has been obtained, gets a period and the user selects the phase
				else:
					# Obtains a reference period
					self.ref_frames, self.settings = get_period(self.ref_frames,{}, framerate=self.framerate)
					#(self.settings).update({'framerate':self.framerate})
					# User selects the period
					self.settings, self.get_period_status = select_period(self.ref_frames,self.settings)

					# If user is happy with period
					if self.get_period_status == 0:
						self.frame_num = 0
						self.initial_process_time = time.time()

			# Clears ref_frames and resets frame number to reselect period
			elif self.get_period_status == 1:
				# Resets frame number
				self.frame_num = 0
				self.ref_frames = np.zeros((self.frame_buffer_length, self.height, self.width), dtype=self.dtype)
				self.get_period_status = 2


			# Once period has been selected, analyses brightfield data for phase triggering
			else:
				# Gets the phase and sad of the current frame 
				pp, self.sad, self.settings = rts.compareFrame(frame, self.ref_frames, settings = self.settings)
				pp = ((pp-self.settings['numExtraRefFrames'])/self.settings['referencePeriod'])*(2*np.pi)#convert phase to 2pi base

				# Gets the current timestamp
				tt = (time.time() - self.initial_process_time)*1000 # Converts time into milliseconds

				# Cumulative Phase
				if self.frame_num != 0:
					wrapped = False
					deltaPhase = pp-self.pp_old
					while deltaPhase<-np.pi:
						wrapped = True
						deltaPhase+= 2*np.pi
					if self.frame_num < self.frame_buffer_length:
						phase = self.frameSummaryHistory[self.frame_num -1,1] + deltaPhase
					else:
						phase = self.frameSummaryHistory[-1,1] + deltaPhase
				else:
					phase = pp
					
				# Clears last entry of framerateSummaryHistory if it exceeds the reference frame length
				if self.frame_num >= self.frame_buffer_length:
					self.frameSummaryHistory = np.roll(self.frameSummaryHistory,-1,axis=0)

				# Gets the argmin of SAD and adds to frameSummaryHistory array
				if self.frame_num < self.frame_buffer_length:
					self.frameSummaryHistory[self.frame_num,:] = tt, phase, np.argmin(self.sad)
				else:
					self.frameSummaryHistory[-1,:] = tt, phase, np.argmin(self.sad)

				# Doesn't predict if haven't done on whole period
				self.pp_old = float(pp)
				self.frame_num +=1
				if self.frame_num-1 > self.settings['referencePeriod']:

					# Gets the trigger response
					if self.frame_num < self.frame_buffer_length:
						trigger_response =  rts.predictTrigger(self.frameSummaryHistory[:self.frame_num,:], self.settings, fitBackToBarrier=True, log=self.log, output="seconds")
					else:
						trigger_response =  rts.predictTrigger(self.frameSummaryHistory, self.settings, fitBackToBarrier=True, log=self.log, output="seconds")
					# frameSummaryHistory is an nx3 array of [timestamp, phase, argmin(SAD)]
					# phase (i.e. frameSummaryHistory[:,1]) should be cumulative 2Pi phase
					# targetSyncPhase should be in [0,2pi]
					
					# Captures the image  and then moves the stage if triggered
					if trigger_response > 0:
						#print('Possible trigger: ',trigger_response,end='\t')
						trigger_response, send, self.settings = rts.gotNewSyncEstimateTimeDelay(tt,trigger_response,self.settings,log=self.log)
						if send>0:
							#print('sending: ',send,end='\t')
							if self.live:

								if self.outputMode == 1:
									trigger_fluorescence_image_capture(tt+trigger_response,self.laser_trigger_pin, self.fluorescence_camera_pins, edge_trigger=False, duration=2000)
								else:
									
									trigger_fluorescence_image_capture(tt+trigger_response,self.laser_trigger_pin, self.fluorescence_camera_pins, edge_trigger=False, duration=2000)
									stage_result = scf.move_stage(self.usb_serial, self.plane_address,self.increment, self.encoding, self.terminator)
							else:
								#print('Not Live: ',send,trigger_response,pp,tt)
								# Returns the trigger response, phase and timestamp for emulated data
								return trigger_response, pp, tt
						
							#self.targetSyncPhaseOld = current_sync_phase
						elif not self.live:
							#print('Not sent: ',send,trigger_response,pp,tt)
							return None, pp, tt

						# Do something with the stage result:
						#	0 = Continue as normal
						#	1 or 2 = Pause capture

					else:
						return None, pp, tt 

		# Logs processing time
		time_fin = time.time()
		(self.time_ary).append(time_fin - time_init)

	# Function to emulate live data
	def emulate(self, video_file, number_of_frames=1000):

#		# Defines initial variables and objects
		emulated_data_set = io.imread(video_file)
		timestamp = []
		phase = []
		process_time = []
		trigger_times = []
		self.live = False
		self.targetSyncPhaseOld = -1 

		#Gets the dimensions of the emulated data and initialises the reference frame array with these dimension
		_, self.height,self.width = emulated_data_set.shape
		self.ref_frames = np.empty((self.frame_buffer_length, self.height, self.width), dtype=self.dtype)
# Defines initial variables and objects
		# DevNote: OpenCV is no longer used
		#emulated_data_set = cv2.VideoCapture(video_file)
       	
	# Gets the dimensions of the emulated data and initialises the reference frame array with these dimension

#		self.width, self.height = (int(emulated_data_set.get(3)), int(emulated_data_set.get(4)))
#
#		self.ref_frames = np.empty((self.frame_buffer_length, self.height, self.width), dtype=self.dtype)

		for i in tqdm(range(number_of_frames)):

			#Trys to emulate actual  fps
			fps_time_init = time.time()

			# Reads a frame from the emulated data set
			frame = emulated_data_set[i,:,:]#.read()[1] #[i,:,:] 
			frame = np.array(frame)

			# Only get responses if a period has been selected and there has been at least 1 period of frames (ie trigger conditions)
			if self.get_period_status == 0 and self.frame_num > self.settings['referencePeriod']:
				# Gets data from analyse function (also times function call)
				time_init = time.time()
				trigger_response, pp, tt = self.analyze(frame)
				#print(tt,pp,trigger_response)
				time_fin = time.time()	

				# Adds data to lists
				if tt != 0:
					process_time.append(time_fin - time_init)
					timestamp.append(tt)
					phase.append(pp)
					# If sucessful trigger response and sync phase has increased
					if trigger_response is not None and trigger_response:# != 0 and current_sync_phase > self.targetSyncPhaseOld:
						trigger_times.append(trigger_response+tt)
						#self.targetSyncPhaseOld = current_sync_phase 
				else:
					print('Warning: this is wrong!')

			# Gets period if trigger conditions are not met
			else:
				self.analyze(frame)

			fps_time_fin = time.time()
			wait_time = 1/self.framerate - (fps_time_fin - fps_time_init)
			if wait_time > 0:
				time.sleep(wait_time)

		# Converts lists to numpy arrays
		process_time = np.array(process_time)
		timestamp = np.array(timestamp)
		phase = np.array(phase)
		trigger_times = np.array(trigger_times)

		print('Processing time (min and max): ',process_time.min(),process_time.max())
		print('Timestamp (min and max): ',timestamp.min(),timestamp.max())
		print('Phase (min and max): ',phase.min(),phase.max())

		# Should have a sawtooth for Phase vs time and scatter points should lie on the saw tooth
		#plt.subplot(2,1,1)
		plt.title('Zebrafish heart phase with simulated trigger fire')
		plt.plot(timestamp, phase, label='Heart phase')
		plt.scatter(trigger_times[0:-1],np.full(len(trigger_times)-1,self.settings['targetSyncPhase']), color='r',label='Simulated trigger fire')
		# Add labels etc
		x1,x2,y1,y2 = plt.axis()
		plt.axis((x1,x2,0,y2*1.1))
		plt.legend()
		plt.xlabel('Time (ms)')
		plt.ylabel('Phase (rad)')

		#Saves the figure
		plt.savefig('simulated_trigger.png',dpi=1000)
		plt.show()


		triggeredPhase = []
		for i in range(len(trigger_times)):

			triggeredPhase.append(phase[(np.abs(timestamp-trigger_times[i])).argmin()])

#		plt.subplot(2,1,2)
		plt.title('Frequency density of triggered phase')
		bins = np.arange(0,2*np.pi, 0.1)
		plt.hist(triggeredPhase, bins=bins, color='g',label='Triggered phase')
		x1,x2,y1,y2 = plt.axis()
		plt.plot(np.full(2,self.settings['targetSyncPhase']),(y1,y2),'r-',label='Target phase')
		plt.xlabel('Triggered phase (rad)')
		plt.ylabel('Frequency')
		plt.legend()
		plt.axis((x1,x2,y1,y2))

		plt.tight_layout()
		plt.show()


# Function that initialises various controlls (pins for triggering laser and fluorescence camera along with the USB for controlling the Newport stages)
def init_controls(laser_trigger_pin, fluorescence_camera_pins, usb_information):

	# Function inputs:
	#	laser_trigger_pin = the GPIO pin number connected to fire the laser
	#	fluorescence_camera_pins = an array of 3 pins used to for the fluoresence camera
	#									(trigger, SYNC-A, SYNC-B)
	#	usb_information = a list containing the information used to set up the usb for controlling the Newport stages
	#							(USB address (str),timeout (flt), baud rate (int), byte size (int), parity (char), stop bits (int), xonxoff (bool))


	# Initialises fastpins module
	try:
		fp.init()
	except Exception as inst:
		print('Error setting up fastpins module.')
		print(inst)
		return 1

	# Sets up laser trigger pin
	try:
		fp.setpin(laser_trigger_pin, 1, 0) #PUD resistor needs to be specified but will be ignored in setup
	except Exception as inst:
		print('Error setting up laser pin.')
		print(inst)
		return 2

	# Sets up fluorescence camera pins
	try:
		fp.setpin(fluorescence_camera_pins[0],1,0) 	#Trigger
		fp.setpin(fluorescence_camera_pins[1],0,0)	#SYNC-A
		fp.setpin(fluorescence_camera_pins[2],0,0)	#SYNC-B
	except Exception as inst:
		print('Error setting up fluorescence camera pins.')
		print(inst)
		return 3

	# Sets up USB for Newport stages
	try:
		ser = serial.Serial(usb_information[0],
						timeout=usb_information[1],
						baudrate=usb_information[2],
						bytesize=usb_information[3],
						parity=usb_information[4],
						stopbits=usb_information[5],
						xonxoff=usb_information[6])
	except Exception as inst:
		print('Error setting up usb.')
		print(inst)
		return 4

	# Serial object is the only new object
	return ser



# Triggers both the laser and fluorescence camera (assumes edge trigger mode by default)
def trigger_fluorescence_image_capture(delay, laser_trigger_pin, fluorescence_camera_pins, edge_trigger=True, duration=100):

	# Function inputs:
	#		delay = delay time (in microseconds) before the image is captured
	#		laser_trigger_pin = the pin number (int) of the laser trigger
	#		fluorescence_camera_pins = an int array containg the triggering, SYNC-A and SYNC-B pin numbers for the fluorescence camera
	#
	# Optional inputs:
	#		edge_trigger:
	#			True = the fluorescence camera captures the image once detecting the start of an increased signal
	#			False = the fluorescence camera captures for the duration of the signal pulse (pulse mode)
	#		duration = (only applies to pulse mode [edge_trigger=False]) the duration (in microseconds) of the pulse

	# Captures an image in edge mode
	if edge_trigger:

		fp.edge(delay, laser_trigger_pin, fluorescence_camera_pins[0], fluorescence_camera_pins[2])

	# Captures in trigger mode
	else:

		fp.pulse(delay, duration, laser_trigger_pin, fluorescence_camera_pins[0])




# Gets the period from sample set
def get_period(brightfield_sequence, settings, framerate=80, minFramesForFit=5, maxRecievedFramesForFit=80, predictionLatency=15):


	# Function inputs
	#		brightfield_sequence = (numpy array) a 3D array of the brightfiled picam data
	#		settings = the settings

	# If the settings are empty creates settings
	if not settings:
		settings = hlp.initialiseSettings(framerate=framerate, referencePeriod=brightfield_sequence.shape[0], minFramesForFit=minFramesForFit,predictionLatency=predictionLatency)

	# Calculates period from getPeriod.py
	brightfield_period, settings = gp.doEstablishPeriodProcessingForFrame(brightfield_sequence, settings)
	settings = rts.deduceBarrierFrameArray(settings)

	# Deletes the contents of the dir before creating a new one 
	# if os.path.isdir('period_data') == True:
	# 	shutil.rmtree('period_data')
	# os.mkdir('period_data')

	# Add new folder with time stamp
	os.mkdirs(os.path.join('period_data',datetime.now.isoformat()),exists_ok=True)

	# Saves the period
	if isinstance(brightfield_period, int) == False:

		for i in range(brightfield_period.shape[0]):
			
			io.imsave(os.path.join('period_data',datetime.now.isoformat(),'/{0:03d}.tiff'.format(i)), brightfield_period[i,:,:])

	return brightfield_period, settings


# Selects the period from a set of reference frames
def select_period(brightfield_period_frames, settings, framerate=80):

	# Function inputs:
	#	brightfield_period_frames = a 3D array consisting of evenly spaced frames containing exactly one period
	#	settings = the settings dictionary (for more information see the helper.py file)

	# Optional inputs:
	#	framerate = the framerate of the brightfield picam (float or int)

	# Defines initial variables
	period_length_in_frames = brightfield_period_frames.shape[0]

	# For now it is a simple command line interface (which is not helpful at all)
	frame = int(input('Please select a frame between 0 and '+str(period_length_in_frames - 1)+'\nOr enter -1 to select a new period.\n'))

	# Checks if user wants to select a new period. Users can use their creative side by selecting any negative number.
	if frame < 0:

		return settings, 1
	
	settings = hlp.updateSettings(settings,referenceFrame=frame)

	return settings, 0

# Defines the three main modes (emulate capture, check fps and live data capture)
def emulate_data_capture():

	#Log status
	log = dict_data['log']

	# Emulated data capture for a set of sample data	
	emulate_data_set = 'sample_data.tif'
	#emulate_data_set = 'sample_data.h264'
	analyse_camera = YUVLumaAnalysis(frame_buffer_length=100,log=log)
	analyse_camera.emulate(emulate_data_set)
	
# Checks that the analyze function can run at the desired framerate
def check_fps( brightfield_framerate=80,brightfield_resolution=128):


	# Defines initial variables
	analyse_time = 10 

	# Sets up basic picam
	camera = picamera.PiCamera()
	camera.framerate = brightfield_framerate
	camera.resolution = (brightfield_resolution,brightfield_resolution)

	# Generate fake reference frame set
	dummy_reference_frames = np.random.randint(0,high=128, size=(10,brightfield_resolution, brightfield_resolution),dtype=np.uint8)

	# Sets up YUVLumaAnalysis object
	analyse_camera = YUVLumaAnalysis(camera=camera, brightfield_framerate=brightfield_framerate, ref_frames=dummy_reference_frames, live=False)

	# Starts analysing brightfield data
	camera.start_recording(analyse_camera, format = 'yuv')
	camera.wait_recording(analyse_time)
	camera.stop_recording()

	# Gets longest time of analyze function 
	longest_analyse_time = max(analyse_camera.time_ary)
	print(longest_analyse_time)

	if 1/longest_analyse_time > brightfield_framerate:
		
		print('Sucess at fps: '+str(brightfield_framerate))
		camera.close()
		return brightfield_framerate,brightfield_resolution 

	else:
		print('Unsucessful at fps: '+str(brightfield_framerate))	
		brightfield_framerate += -10
		camera.close()
		check_fps(brightfield_framerate=brightfield_framerate)	
		
# Performs a live capture of the data
def live_data_capture():


	# Defines initial variables
	log = dict_data['log']
	laser_trigger_pin = dict_data['laser_trigger_pin']
	fluorescence_camera_pins = dict_data['fluorescence_camera_pins'] # Trigger, SYNC-A, SYNC-B
	usb_information = (dict_data['usb_name'],dict_data['usb_timeout'],dict_data['usb_baudrate'],dict_data['usb_dataBits'],dict_data['usb_parity'],dict_data['usb_XOnOff'],True)	#USB address, timeout, baud rate, data bits, parity, Xon/Xoff

	#Defines variables for USB serial stage commands
	plane_address = dict_data['plane_address']
	encoding = dict_data['encoding']
	terminator = chr(dict_data['terminators'][0])+chr(dict_data['terminators'][1])
	increment = dict_data['increment']

	# Sets up basic picam
	brightfield_resolution = dict_data['brightfield_resolution'] 
	brightfield_framerate = dict_data['brightfield_framerate']

	analyse_time = dict_data['analyse_time']  # s 

	camera = picamera.PiCamera()
	camera.framerate = brightfield_framerate
	camera.resolution = (brightfield_resolution,brightfield_resolution)
	camera.awb_mode = dict_data['awb_mode'] 
	camera.exposure_mode = dict_data['exposure_mode']
	camera.shutter_speed = dict_data['shutter_speed']  # us
	camera.image_denoise = dict_data['image_denoise']

	# Starts preview
	camera.start_preview(fullscreen=False, window = (500,20,640,480))

	if dict_data['output_mode'] == '5V_BNC_Only':
		
		# USB serial not used so set to None
		usb_serial = None

	else:
		
		# Sets up pins and usb
		usb_serial = init_controls(laser_trigger_pin, fluorescence_camera_pins, usb_information)

		# Checks if usb_serial has recieved an error code
		if isinstance(usb_serial, int):
			print('Error code '+str(usb_serial))
			sys.exit()

		# Sets up stage to recieve input
		neg_limit, pos_limit, current_position = scf.set_user_stage_limits(usb_serial,plane_address,encoding,terminator)
		input('Press any key once the heart is in position.')

	# Sets up YUVLumaAnalysis object
	analyse_camera = YUVLumaAnalysis(camera=camera, brightfield_framerate=brightfield_framerate, usb_serial=usb_serial,log=log )

	# Starts analysing brightfield data
	camera.start_recording(analyse_camera, format = 'yuv')
	camera.wait_recording(analyse_time)
	camera.stop_recording()

	# Ends preview
	input('Press any key to end camera preview')
	camera.stop_preview()

	# Iterates through a sample stack (with no period)
#	neg_limit = 0
#	pos_limit = 4 
#	current_position = 0
#	increment = 0.01
#	plane_address = 1
#	encoding = 'utf-8'
#	terminator = chr(13)+chr(10)
#	delay = 400000
#	duration = 20000
#
#	for i in tqdm(range(neg_limit, int(pos_limit/increment))):
#
#		trigger_fluorescence_image_capture(delay, laser_trigger_pin, fluorescence_camera_pins, edge_trigger=False, duration=duration)
#		stage_result = scf.move_stage(usb_serial, plane_address,increment, encoding, terminator)
#	scf.move_stage(usb_serial, plane_address, pos_limit*(-1), encoding, terminator)


# Reads data from json file
data_file = open("settings.json")
dict_data = json.load(data_file)	

# Sets the prediction latency
predictionLatency = dict_data['predictionLatency']

# Performs a live or emulated data capture
live_capture = dict_data['live']
if live_capture == True:
	live_data_capture()
else:
	emulate_data_capture()
