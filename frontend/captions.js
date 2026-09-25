export function createCaptionView(element, language) {
  const finalLines = [];
  let preview = "";

  function render() {
    element.replaceChildren();
    for (const text of finalLines) {
      const line = document.createElement("p");
      line.textContent = text;
      element.append(line);
    }
    if (preview) {
      const line = document.createElement("p");
      line.className = "preview";
      line.textContent = preview;
      element.append(line);
    }
  }

  return {
    accept(event) {
      if (event.language !== language) return;
      if (event.is_final) {
        finalLines.push(event.text);
        if (finalLines.length > 5) finalLines.shift();
        preview = "";
      } else {
        preview = event.text;
      }
      render();
    },
    clear() {
      finalLines.length = 0;
      preview = "";
      render();
    },
    render,
  };
}
