<script>
  export let name;
  const paths = {
    up: 'M8 13V3 M3 8l5-5 5 5',
    down: 'M8 3v10 M3 8l5 5 5-5',
    check: 'M3 8l3 3 7-7',
    duplicate: 'M6 6h7v7H6z M3 10V3h7',
    plus: 'M8 3v10 M3 8h10',
    close: 'M4 4l8 8 M12 4l-8 8',
    grip: 'M5 3h.01 M11 3h.01 M5 8h.01 M11 8h.01 M5 13h.01 M11 13h.01',
    // Magnifier with + : zoom to the membrane. Magnifier with a frame: the whole image.
    'zoom-membrane': 'M7 2.5a4.5 4.5 0 1 0 0 9a4.5 4.5 0 1 0 0-9z M10.3 10.3L14 14 M7 5v4 M5 7h4',
    'zoom-whole': 'M7 2.5a4.5 4.5 0 1 0 0 9a4.5 4.5 0 1 0 0-9z M10.3 10.3L14 14 M5 5.3h4v3.4H5z',
  };
</script>

<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>
