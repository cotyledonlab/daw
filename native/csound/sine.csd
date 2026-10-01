<CsoundSynthesizer>
<CsOptions>
</CsOptions>
<CsInstruments>
sr = 48000
ksmps = 1
nchnls = 2
0dbfs = 1

instr 1
    aSig oscili p5, p4
    outs aSig, aSig
endin
</CsInstruments>
<CsScore>
i1 0 1 440 .1
e
</CsScore>
</CsoundSynthesizer>
