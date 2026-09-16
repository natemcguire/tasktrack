"use strict";
const frame = document.querySelector("iframe");
function focusScreen() {
  if (/^#[a-z-]+$/.test(location.hash))
    frame.src = frame.src.split("#")[0] + location.hash;
}
focusScreen();
addEventListener("hashchange", focusScreen);
