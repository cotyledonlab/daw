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
chn_k "amplitude", 1
instr 1
  kFrequency chnget "frequency"
  kGain chnget "amplitude"
  aTone oscili kGain, kFrequency
  outs aTone, aTone
endin
</CsInstruments>
<CsScore>
i1 0 1
e
</CsScore>
</CsoundSynthesizer>
