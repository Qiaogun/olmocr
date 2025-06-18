const dropZone = document.getElementById('drop-zone');
const results = document.getElementById('results');
const allDownloadBtn = document.getElementById('all-download');
let pages = [];

function preventDefaults(e) {
  e.preventDefault();
  e.stopPropagation();
}
['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
  dropZone.addEventListener(eventName, preventDefaults, false);
});
['dragenter', 'dragover'].forEach(eventName => {
  dropZone.addEventListener(eventName, () => dropZone.classList.add('hover'), false);
});
['dragleave', 'drop'].forEach(eventName => {
  dropZone.addEventListener(eventName, () => dropZone.classList.remove('hover'), false);
});

dropZone.addEventListener('drop', handleDrop, false);

function handleDrop(e) {
  const dt = e.dataTransfer;
  const file = dt.files[0];
  if (!file) return;
  uploadPdf(file);
}

function uploadPdf(file) {
  const formData = new FormData();
  formData.append('pdf', file);
  fetch('/api/ocr', { method: 'POST', body: formData })
    .then(res => res.json())
    .then(data => {
      pages = data.pages || [];
      renderPages();
    })
    .catch(err => console.error('Upload error', err));
}

function renderPages() {
  results.innerHTML = '';
  pages.forEach((text, i) => {
    const div = document.createElement('div');
    div.className = 'page-result';
    div.innerHTML = `<h3>Page ${i + 1}</h3><pre>${text}</pre>`;
    const actions = document.createElement('div');
    actions.className = 'page-actions';
    const copyBtn = document.createElement('button');
    copyBtn.textContent = 'Copy MD';
    copyBtn.onclick = () => navigator.clipboard.writeText(text);
    const downloadBtn = document.createElement('button');
    downloadBtn.textContent = 'Download MD';
    downloadBtn.onclick = () => downloadText(`page_${i + 1}.md`, text);
    actions.appendChild(copyBtn);
    actions.appendChild(downloadBtn);
    div.appendChild(actions);
    results.appendChild(div);
  });
}

function downloadText(filename, text) {
  const blob = new Blob([text], {type: 'text/markdown'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

allDownloadBtn.onclick = function() {
  if (!pages.length) return;
  const zip = new JSZip();
  pages.forEach((text, i) => zip.file(`page_${i + 1}.md`, text));
  zip.generateAsync({type:'blob'}).then(blob => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'pages.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });
};
