"""Extension of CLI Open Optical Gating System for a remote client connecting over WebSockets"""

# Python imports
import sys
import json
import time

# Module imports
from loguru import logger
from skimage import io
import websockets, asyncio

# Local imports
import open_optical_gating.cli.optical_gater_server as server
from pixelarray import PixelArray
import sockets_comms as comms



class WebSocketOpticalGater(server.OpticalGater):
    """Extends the optical gater server for a remote client connecting over WebSockets
    """

    def __init__(
        self, settings=None, ref_frames=None, ref_frame_period=None
    ):
        """Function inputs:
            settings      dict  Parameters affecting operation (see default_settings.json)
        """
        
        # JT TODO: this is very temporary - for now the superclass requires this to be defined.
        # We either need to get the client tell us the framerate or remove the need
        # for this attribute entirely, and deduce the framerate from individual frame timestamps.
        # The latter would be better, as long as the timestamps on individual frames are reliable.
        # We need to make sure they are, though, or our predictions will be off anyway!
        self.framerate = 80

        # Initialise parent
        super(WebSocketOpticalGater, self).__init__(
            settings=settings,
            ref_frames=ref_frames,
            ref_frame_period=ref_frame_period,
        )
    
    async def received_message(self, websocket):
        # Wait for the next message from the remote client
        rawMessage = await websocket.recv()
        message = comms.DecodeMessage(rawMessage)

        if not "type" in message:
            logger.critical("Ignoring unknown message with no 'type' specifier. Message was {0}".format(message))
        elif message["type"] == "frame":
            # Do the synchronization analysis on the frame in this message
            pixelArrayObject = comms.ParseFrameMessage(message)
            if "sync" in pixelArrayObject.metadata:
                logger.critical("Received a frame that already has 'sync' metadata. We will overwrite this!")
            pixelArrayObject.metadata["sync"] = dict()
            
            # JT TODO: for now I just hack self.width and self.height, but this should get fixed as part of the PixelArray refactor
            self.height, self.width = pixelArrayObject.shape
            (trigger_response, current_phase, current_time_s) = self.analyze(pixelArrayObject)
            
            # JT TODO: this should be done in the base class, as part of the PixelArray refactor
            if (trigger_response is not None):
                pixelArrayObject.metadata["sync"]["send_trigger"] = 1
                pixelArrayObject.metadata["sync"]["trigger_time"] = current_time_s + trigger_response
            else:
                pixelArrayObject.metadata["sync"]["send_trigger"] = 0
                pixelArrayObject.metadata["sync"]["trigger_time"] = 0
            pixelArrayObject.metadata["sync"]["phase"] = current_phase
            
            # Send back to the client whatever metadata we have added to the frame as part of the sync analysis.
            # This will include whether or not a trigger is predicted, and when.
            returnMessage = comms.EncodeFrameResponseMessage(pixelArrayObject.metadata["sync"])
            await websocket.send(returnMessage)
        else:
            logger.critical("Ignoring unknown message of type {0}".format(message["type"]))

    def run_server(self, host="localhost", port=8765):
        """ Blocking call that runs the WebSockets server, acting on client messages (mostly frames, probably)
            Function inputs:
              host          str   Host address to use for socket server
              port          int   Port to use for socket server
            """
        start_server = websockets.serve(lambda ws, p: self.received_message(ws), "localhost", 8765)
        asyncio.get_event_loop().run_until_complete(start_server)
        asyncio.get_event_loop().run_forever()

def run(settings):
    logger.success("Initialising gater...")
    analyser = WebSocketOpticalGater(settings=settings)
    logger.success("Running server...")
    analyser.run_server()


if __name__ == "__main__":
    t = time.time()
    # Reads data from settings json file
    if len(sys.argv) > 1:
        settings_file = sys.argv[1]
    else:
        settings_file = "settings.json"

    with open(settings_file) as data_file:
        settings = json.load(data_file)

    # Runs the server
    run(settings)
