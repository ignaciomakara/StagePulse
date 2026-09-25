class StagePulseCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channels = inputs[0];
    if (channels && channels.length) {
      const mono = new Float32Array(channels[0].length);
      for (let channel = 0; channel < channels.length; channel++) {
        const samples = channels[channel];
        for (let index = 0; index < mono.length; index++) {
          mono[index] += samples[index] / channels.length;
        }
      }
      this.port.postMessage(mono, [mono.buffer]);
    }
    return true;
  }
}

registerProcessor("stagepulse-capture", StagePulseCapture);
