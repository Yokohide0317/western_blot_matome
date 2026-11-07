# WB workbench UI review

Design review based on [unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The screen is a laboratory image workbench. Its character is quiet, precise and legible. White and graphite surfaces keep attention on the blot and Color images; teal indicates selection. Membrane identifiers sit below the images, at the right edge of each row.

The audit found decorative text symbols, competing colored status badges, inconsistent control spacing, and reduced opacity on entire inactive rows. The last issue changed how the scientific images appeared, so only inactive controls are now dimmed. Images retain their full displayed contrast.

Changes follow the requested workflow: hidden rows are restored from membrane selection; band selection contains only included rows; one lane has the same width across rows and exports. Legacy full-width settings migrate on load. Lane controls remain immediately above the edited row. M1/M2/M3 identify the source membrane independently of panel labels such as A/B/C.

The polish uses a shared small line-icon component, tabular figures, restrained status text, neutral surfaces and consistent spacing. Existing system fonts and the two-stage layout remain. The actual experimental images provide the visual content; no generated images or decorative motion were added. Membrane identifiers are interface annotations, not additions to the exported blot pixels.

Membrane selection now contains only membrane controls. Its switches are independent of Selection checkboxes, including when a membrane has zero Selections. All Selection work happens in band selection: drag to create, duplicate the exact crop and exposure, delete with the cross, and choose output inclusion with a checkbox. Candidate-row overlays and commands have been removed. Export uses the saved edits directly without approval controls or draft labels.
