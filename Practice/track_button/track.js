let trackButton = document.getElementById('track-button');
let trackText = document.getElementById('track-text');

if (!trackButton) {
  trackButton = document.createElement('button');
  trackButton.id = 'track-button';
  trackButton.textContent = 'Click me!!';
  document.body.appendChild(trackButton);
}

if (!trackText) {
  trackText = document.createElement('div');
  trackText.id = 'track-text';
  document.body.appendChild(trackText);
}

trackButton.onclick = () => {
  trackText.textContent = 'hello track';
};
