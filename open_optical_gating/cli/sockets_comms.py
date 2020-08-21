"""Functions for working with messages to be exchanged over WebSockets using our protocol.

    All messages consist of a dictionary of key/value pairs, as follows.
    Messages are CBOR-encoded for transmission over WebSockets.
    (The Python code in this module can also be switched to use JSON encoding, for ease of debugging)
    
    Messages:
        "Frame to process"   
        Sent from client->server to provide a new brightfield frame for analysis.
        Dictionary containing:
            "type"    ="frame"
            "frame"   list of:
                [0]    list  Image dimensions, represented as a list of: [height,width]
                [1]    str   Pixel data type. Recommended: "uint8". Also supported: "uint16".
                [2]    bytes Array data. Raw pixel data, in row-major order.
                              [If transmitting using JSON encoding, pixel data is base64 encoded]
                              TODO: endianness is not currently considered.
                               Data should be transmitted in machine-native endianness,
                               and transmission between different machines of different
                               endianness is not supported.
                [3]    dict  Frame metadata (see below)
                
        "Sync response"
        Sent from server->client in response to a "frame" message.
        Dictionary containing:
            "type"     ="sync"
            "sync"     dict  Synchronization metadata (see below)
        
        
    Frame metadata:
        Dictionary keys that we will pay attention to are:
            "timestamp"      Timestamp associated with the frame.
                             This can be in any timebase (e.g. computer, camera hardware, ...),
                             and predicted future trigger times will be computed and returned
                             in that same timebase.
                             
    Synchronization metadata (after analysing the most recent frame)
            "send_trigger"   int [0,1]     Our code has decided that a synchronization trigger should be generated
            "trigger_time"   float         Future time prediction (in frame timebase) for the next synchronization trigger.
            "phase"          float [0,2pi) Our computed phase (0 to 2pi) for the most recent frame
"""

from pixelarray import ArrayJSONDecode, ArrayCBORDecode

useCBOR = True

# I do a conditional import here, rather than importing both modules.
# That's just to catch if we do anything crazy like use json when we are in cbor mode.
if useCBOR:
    import cbor
else:
    # We use 'orjson', because dumps() and loads() are perhaps 40% faster than with vanilla 'json'
    import orjson as json

def DecodeMessage(message):
    """ Function inputs:
            message   bytes    JSON- or CBOR-encoded data received as a WebSockets message
        Returns:
            dict representing the decoded message
    """
    if useCBOR:
        return cbor.loads(message)
    else:
        return json.loads(message)

def ParseFrameMessage(message):
    """ Function inputs:
            message   dict    WebSockets message known to be a Frame message within our protocol
        Returns:
            New PixelArray object
    """
    return DecodeArray(message["frame"])

def DecodeArray(arrayEncoded):
    """ Function inputs:
            arrayEncoded   bytes    JSON or CBOR-encoded message data, known to represent a PixelArray
        Returns:
            New PixelArray object
    """
    if useCBOR:
        return ArrayCBORDecode(arrayEncoded)
    else:
        return ArrayJSONDecode(arrayEncoded)


def EncodeMessage(message):
    """ Function inputs:
            message   dict,list,etc    JSON-encodable object to be sent as a WebSockets message
        Returns:
            string to be sent over WebSockets
    """
    if useCBOR:
        return cbor.dumps(message)
    else:
        return json.dumps(message)

def EncodeFrameMessage(arrayObject):
    """ Function inputs:
            arrayObject   PixelArray    Frame+metadata to send in a message
        Returns:
            string to be sent over WebSockets
    """
    return EncodeMessage({"type": "frame", "frame": arrayObject.for_cbor()})

def EncodeFrameResponseMessage(syncMetadata):
    """ Function inputs:
            syncMetadata  dict          Metadata generated as part of the sync analysis.
                                        This will include the trigger prediction
        Returns:
            string to be sent over WebSockets
    """
    return EncodeMessage({"type": "sync", "sync": syncMetadata})
