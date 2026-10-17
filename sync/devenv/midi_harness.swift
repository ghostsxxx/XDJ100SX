// Virtual MIDI device standing in for the XDJ100SX hardware (Teensy), so
// tests can press PLAY, move the pitch fader or turn the jog wheel.
//
// Creates a CoreMIDI source named NAME and sends what it reads from FIFO,
// one message per line, hex bytes:   90 3C 7F   (note on, PLAY)
//
// Usage: swift midi_harness.swift NAME FIFO
import CoreMIDI
import Foundation

let args = CommandLine.arguments
guard args.count == 3 else {
    print("usage: midi_harness.swift NAME FIFO")
    exit(2)
}
let name = args[1] as CFString
let fifoPath = args[2]

var client = MIDIClientRef()
var source = MIDIEndpointRef()
guard MIDIClientCreate(name, nil, nil, &client) == noErr,
      MIDISourceCreate(client, name, &source) == noErr else {
    print("cannot create MIDI source")
    exit(1)
}
print("MIDI source ready: \(args[1])")
fflush(stdout)

func send(_ bytes: [UInt8]) {
    var packetList = MIDIPacketList()
    let packet = MIDIPacketListInit(&packetList)
    _ = MIDIPacketListAdd(&packetList, MemoryLayout<MIDIPacketList>.size, packet, 0,
                          bytes.count, bytes)
    MIDIReceived(source, &packetList)
}

// Reopen the FIFO after each writer closes it, so commands can come from
// separate `echo ... > FIFO` calls.
while true {
    guard let handle = FileHandle(forReadingAtPath: fifoPath) else {
        print("cannot open \(fifoPath)")
        exit(1)
    }
    let text = String(decoding: handle.readDataToEndOfFile(), as: UTF8.self)
    handle.closeFile()
    for line in text.split(separator: "\n") {
        let bytes = line.split(separator: " ").compactMap { UInt8($0, radix: 16) }
        if !bytes.isEmpty {
            send(bytes)
        }
    }
}
