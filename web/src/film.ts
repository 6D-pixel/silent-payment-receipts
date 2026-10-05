// The video at the top of the story: plays muted on its own, with buttons to hear it, pause it or skip it.

const video = document.getElementById("film") as HTMLVideoElement;
const sound = document.getElementById("film-sound") as HTMLButtonElement;
const pause = document.getElementById("film-pause") as HTMLButtonElement;
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;

let shown = false;
let onScreen = true;
let stopped = calm;  // paused by the viewer (or never started, for reduced motion)

function render(): void {
  sound.textContent = video.muted ? "Play with sound" : "Mute";
  pause.textContent = video.paused ? "Play" : "Pause";
}

function play(): void {
  video.play().catch(() => {
    // The browser blocked it. Without sound it may still play; otherwise the Play button works.
    if (!video.muted) { video.muted = true; play(); }
  }).finally(render);
}

// The page is one sticky nav plus this section: the video fills the rest of the screen.
function fit(): void {
  const nav = document.querySelector<HTMLElement>(".tabs");
  document.documentElement.style.setProperty("--nav-h", `${nav?.offsetHeight ?? 0}px`);
}

sound.addEventListener("click", () => {
  if (video.muted) {
    video.currentTime = 0;
    video.muted = false;
    stopped = false;
    play();
  } else {
    video.muted = true;
    render();
  }
});
pause.addEventListener("click", () => {
  stopped = !video.paused;
  if (stopped) video.pause();
  else play();
  render();
});
document.getElementById("film-skip")!.addEventListener("click", () => {
  document.getElementById("story-start")!.scrollIntoView({ behavior: calm ? "auto" : "smooth" });
});

// With sound it plays once; muted, it keeps looping quietly.
video.addEventListener("ended", () => {
  video.muted = true;
  video.currentTime = 0;
  if (!stopped && shown && onScreen) play();
  render();
});
video.addEventListener("play", render);
video.addEventListener("pause", render);

// Muted, it only plays while it is on screen.
new IntersectionObserver(([entry]) => {
  onScreen = entry.isIntersecting;
  if (!video.muted) return;
  if (!onScreen) video.pause();
  else if (shown && !stopped) play();
}).observe(video);

addEventListener("resize", fit);
fit();
render();

/** Called by the router: pause when the story is hidden, carry on when it comes back. */
export function showFilm(visible: boolean): void {
  shown = visible;
  if (!visible) video.pause();
  else if (!stopped && onScreen) play();
}
