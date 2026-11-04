class VoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(4096);
    this.used = 0;
    this.stopped = false;
    this.port.onmessage = (event) => {
      if (event.data === "stop") {
        this.stopped = true;
        this.flush();
        this.port.postMessage({type: "stopped"});
      }
    };
  }

  flush() {
    if (this.used) {
      const chunk = this.buffer.slice(0, this.used);
      this.port.postMessage({type: "chunk", samples: chunk}, [chunk.buffer]);
      this.used = 0;
    }
  }

  process(inputs) {
    if (this.stopped) return false;
    const samples = inputs[0]?.[0];
    if (samples) {
      for (const value of samples) {
        this.buffer[this.used++] = value;
        if (this.used === this.buffer.length) this.flush();
      }
    }
    return true;
  }
}

registerProcessor("voice-capture", VoiceCapture);
