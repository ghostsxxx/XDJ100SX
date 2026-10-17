// Records an audio input device to a WAV file, gap-free (AVAudioEngine).
// Used to record the players' output via a loopback device (BlackHole).
//
// Usage: swift record_device.swift "BlackHole 2ch" SECONDS OUT.wav
import AVFoundation
import CoreAudio

func deviceID(named name: String) -> AudioDeviceID? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size)
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids)
    for id in ids {
        var nameAddress = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var cfName: Unmanaged<CFString>?
        var nameSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        if AudioObjectGetPropertyData(id, &nameAddress, 0, nil, &nameSize, &cfName) == noErr,
           let n = cfName?.takeRetainedValue() as String?, n == name {
            return id
        }
    }
    return nil
}

let args = CommandLine.arguments
guard args.count == 4, let seconds = Double(args[2]) else {
    print("usage: record_device.swift DEVICE SECONDS OUT.wav"); exit(2)
}
guard var device = deviceID(named: args[1]) else { print("device not found: \(args[1])"); exit(1) }

let engine = AVAudioEngine()
let input = engine.inputNode
let status = AudioUnitSetProperty(input.audioUnit!, kAudioOutputUnitProperty_CurrentDevice,
                                  kAudioUnitScope_Global, 0, &device, UInt32(MemoryLayout<AudioDeviceID>.size))
guard status == noErr else { print("cannot select device: \(status)"); exit(1) }

let format = input.outputFormat(forBus: 0)
var file: AVAudioFile? = try AVAudioFile(forWriting: URL(fileURLWithPath: args[3]),
                           settings: [AVFormatIDKey: kAudioFormatLinearPCM,
                                      AVSampleRateKey: format.sampleRate,
                                      AVNumberOfChannelsKey: format.channelCount,
                                      AVLinearPCMBitDepthKey: 16,
                                      AVLinearPCMIsFloatKey: false])
var lastTime: AVAudioFramePosition?
var gaps = 0
input.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, time in
    // Detect dropped input: sample times must be contiguous.
    if let last = lastTime, time.sampleTime != last { gaps += 1 }
    lastTime = time.sampleTime + AVAudioFramePosition(buffer.frameLength)
    try? file?.write(from: buffer)
}
try engine.start()
// Keep the run loop alive; a blocking sleep starves the tap.
RunLoop.main.run(until: Date(timeIntervalSinceNow: seconds))
engine.stop()
input.removeTap(onBus: 0)
let frames = file?.length ?? 0
file = nil  // closes the file and finalizes the WAV header
print("recorded \(frames) frames at \(format.sampleRate) Hz, gaps: \(gaps)")
