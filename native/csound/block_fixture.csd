<CsoundSynthesizer>
<CsOptions>
</CsOptions>
<CsInstruments>
sr = 48000
ksmps = 64
nchnls = 2
nchnls_i = 2
0dbfs = 1
chn_k "frequency", 1
chn_k "gain", 1
instr 1
  kFrequency chnget "frequency"
  kGain chnget "gain"
  aTone oscili kGain, kFrequency
  aLeft, aRight ins
  outs aTone + aLeft, aTone + aRight
endin
</CsInstruments>
<CsScore>
i1 0 1.024
e
</CsScore>
</CsoundSynthesizer>
