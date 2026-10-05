// The video at the top of the story. It tries to start with sound; browsers often refuse that until the
// viewer has clicked something, so then it plays muted and the speaker button (or a click on the video)
// turns the sound on.

const video = document.getElementById("film") as HTMLVideoElement;
const sound = document.getElementById("film-sound") as HTMLButtonElement;
const soundLabel = document.getElementById("film-sound-label")!;
const pause = document.getElementById("film-pause") as HTMLButtonElement;
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;

let shown = false;
let onScreen = true;
let stopped = calm;   // paused by the viewer (or never started, for reduced motion)
let fromStart = true; // turning the sound on starts the narration from the beginning, unless it is already heard

function render(): void {
  sound.dataset.muted = String(video.muted);
  soundLabel.textContent = video.muted ? "Turn sound on" : "Mute";
  sound.setAttribute("aria-label", video.muted ? "Turn sound on" : "Mute the video");
  pause.textContent = video.paused ? "Play" : "Pause";
}

function play(): void {
  video.play().then(() => {
    if (!video.muted) fromStart = false;
  }).catch(() => {
    // The browser blocked it. Without sound it may still play; otherwise the Play button works.
    if (!video.muted) { video.muted = true; play(); }
  }).finally(render);
}

function soundOn(): void {
  if (fromStart) video.currentTime = 0;
  video.muted = false;
  stopped = false;
  play();
}

// The page is one sticky nav plus this section: the video fills the rest of the screen.
function fit(): void {
  const nav = document.querySelector<HTMLElement>(".tabs");
  document.documentElement.style.setProperty("--nav-h", `${nav?.offsetHeight ?? 0}px`);
}

sound.addEventListener("click", () => {
  if (video.muted) soundOn();
  else { video.muted = true; render(); }
});
video.addEventListener("click", () => { if (video.muted) soundOn(); });
pause.addEventListener("click", () => {
  stopped = !video.paused;
  if (stopped) video.pause();
  else play();
  render();
});
document.getElementById("film-skip")!.addEventListener("click", () => {
  document.getElementById("story-start")!.scrollIntoView({ behavior: calm ? "auto" : "smooth" });
});

// With sound it plays once; then it keeps looping quietly.
video.addEventListener("ended", () => {
  video.muted = true;
  fromStart = true;
  video.currentTime = 0;
  if (!stopped && shown && onScreen) play();
  render();
});
video.addEventListener("play", render);
video.addEventListener("pause", render);
video.addEventListener("volumechange", render);

// Muted, it only plays while it is on screen.
new IntersectionObserver(([entry]) => {
  onScreen = entry.isIntersecting;
  if (!video.muted) return;
  if (!onScreen) video.pause();
  else if (shown && !stopped) play();
}).observe(video);

addEventListener("resize", fit);
fit();
if (!calm) video.muted = false;  // ask for sound first; play() falls back to muted
render();

/** Called by the router: pause when the story is hidden, carry on when it comes back. */
export function showFilm(visible: boolean): void {
  shown = visible;
  if (!visible) video.pause();
  else if (!stopped && onScreen) play();
}
