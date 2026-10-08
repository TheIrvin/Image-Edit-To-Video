param([string]$RequestPath, [string]$ListPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($ListPath) {
        $voices = @($speaker.GetInstalledVoices() | ForEach-Object {
            @{ name = $_.VoiceInfo.Name; language = $_.VoiceInfo.Culture.Name; enabled = $_.Enabled }
        })
        [IO.File]::WriteAllText($ListPath, (ConvertTo-Json -InputObject $voices), [Text.UTF8Encoding]::new($false))
        exit 0
    }
    $request = [IO.File]::ReadAllText($RequestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $speaker.SelectVoice($request.voice)
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $index = 0
    foreach ($item in $request.items) {
        $speaker.SetOutputToWaveFile($item.output, $format)
        $speaker.Speak($item.text)
        $speaker.SetOutputToNull()
        $index++
        $progress = @{ done = $index; total = $request.items.Count; message = ('Narrando escena ' + $index) } | ConvertTo-Json
        [IO.File]::WriteAllText($request.progress, $progress, [Text.UTF8Encoding]::new($false))
    }
} finally { $speaker.Dispose() }
