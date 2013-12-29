import itertools
import pyaudio

from opus import encoder, decoder
from . import threads


class GenericStream(threads.SafeThread):

    def __init__(self):
        super().__init__()

        self.format = pyaudio.paInt16
        self.channels = 1
        self.bitrate = 12000
        assert not self.bitrate % 50, "Bitrate must be divisible by 50"
        self.chunk = self.bitrate // 50

        self.pyaudio = pyaudio.PyAudio()
        self.compress = False

    @property
    def chunk_size(self):
        return (self.chunk * self.pyaudio.get_sample_size(self.format)
                * self.channels)


class MicrophoneStream(GenericStream):

    def __init__(self):
        super().__init__()

        self.stream = self.pyaudio.open(format=self.format,
                                        channels=self.channels,
                                        rate=self.bitrate,
                                        input=True,
                                        frames_per_buffer=self.chunk)

        self._encoder = encoder.Encoder(self.bitrate, self.channels, 'voip')
        self.start()

    def encoder(self, data):
        return self._encoder.encode(data, self.chunk)

    def get_task(self, interval, queue_):
        return self.stream.read(self.chunk)

    def handle_task(self, task, queue):
        queue.put(task)

    def encode(self, clear=True):
        encoder = self.encoder

        with self.out_queue.mutex:
            queue = self.out_queue.queue

            if not queue:
                return b''

            encoded_data = encoder(b''.join(queue))

            if clear:
                queue.clear()

        return encoded_data


class SpeakerStream(GenericStream):

    def __init__(self):
        super().__init__()

        self.stream = self.pyaudio.open(format=self.format,
                channels=self.channels,
                rate=self.bitrate,
                output=True,
                frames_per_buffer=self.chunk)

        self._decoder = decoder.Decoder(self.bitrate, self.channels)
        self.start()

    def decoder(self, data):
        return self._decoder.decode(data, self.chunk)

    def slice_bytes(self, total, iterable):
        iterator = iter(iterable)

        while True:
            chunk = bytes(itertools.islice(iterator, total))
            if not chunk:
                return

            yield chunk

    def handle_task(self, task, queue):
        self.stream.write(task)

    def decode(self, data, clear=True):
        if clear:
            with self.in_queue.mutex:
                self.in_queue.queue.clear()

        decoder = self.decoder
        data = decoder(data)
        for chunk in self.slice_bytes(self.chunk_size, data):
            self.in_queue.put(chunk)
