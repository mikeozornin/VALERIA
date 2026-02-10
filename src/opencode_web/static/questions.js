// Keep each form mounted while streaming messages and polling pending requests.
export function questionPanel(target, {fetchQuestions, reply, onWaiting}) {
  const cards = new Map();
  const error = document.createElement("div");
  error.className = "error question-error";
  error.setAttribute("role", "status");
  target.append(error);
  let loading = false;
  let version = 0;

  function card(request) {
    const form = document.createElement("form");
    form.className = "question-card";
    form.dataset.questionId = request.id;
    const title = document.createElement("strong");
    title.textContent = "OpenCode ожидает вашего ответа";
    form.append(title);
    const groups = request.questions.map((question, index) => {
      const group = document.createElement("fieldset");
      const legend = document.createElement("legend");
      legend.textContent = question.header || `Вопрос ${index + 1}`;
      const text = document.createElement("p");
      text.textContent = question.question;
      group.append(legend, text);
      const choices = [];
      function choice(label, description, custom = false) {
        const row = document.createElement("label");
        row.className = "question-option";
        const input = document.createElement("input");
        input.type = question.multiple ? "checkbox" : "radio";
        input.name = `answer-${index}`;
        const caption = document.createElement("span");
        const name = document.createElement("strong");
        name.textContent = label;
        caption.append(name);
        if (description) {
          const hint = document.createElement("span");
          hint.className = "hint";
          hint.textContent = description;
          caption.append(hint);
        }
        row.append(input, caption);
        group.append(row);
        let entry;
        if (custom) {
          entry = document.createElement("textarea");
          entry.className = "input";
          entry.maxLength = 10000;
          entry.setAttribute("aria-label", `Свой ответ: ${question.header || index + 1}`);
          entry.placeholder = "Введите свой ответ";
          entry.addEventListener("input", () => { input.checked = true; });
          group.append(entry);
        }
        choices.push({input, label, entry});
      }
      for (const option of question.options || []) choice(option.label, option.description);
      if (question.custom !== false) choice("Свой ответ", "", true);
      form.append(group);
      return choices;
    });
    const message = document.createElement("div");
    message.className = "error";
    message.setAttribute("role", "status");
    const button = document.createElement("button");
    button.type = "submit";
    button.className = "btn btn-primary";
    button.textContent = "Отправить ответ";
    form.append(message, button);
    let submitting = false;
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (submitting) return;
      const answers = groups.map(choices => choices.filter(x => x.input.checked).map(x => x.entry ? x.entry.value.trim() : x.label));
      if (answers.some(values => !values.length || values.some(value => !value))) {
        message.textContent = "Ответьте на каждый вопрос: выберите вариант или заполните свой ответ";
        return;
      }
      submitting = true;
      version += 1; // Ignore a poll started before the reply.
      message.textContent = "";
      const controls = form.querySelectorAll("input, textarea, button");
      controls.forEach(input => { input.disabled = true; });
      button.textContent = "Отправляем…";
      let closed = false;
      try {
        await reply(request.id, answers);
        closed = true;
      } catch (err) {
        if (err.status === 409) closed = true;
        else message.textContent = err.message;
      } finally {
        version += 1;
        if (closed) {
          form.remove(); cards.delete(request.id);
          onWaiting(cards.size > 0);
        } else {
          submitting = false;
          controls.forEach(input => { input.disabled = false; });
          button.textContent = "Отправить ответ";
        }
        void refresh();
      }
    });
    return form;
  }

  async function refresh() {
    if (loading || !target.isConnected) return;
    loading = true;
    const started = version;
    try {
      const pending = await fetchQuestions();
      if (!target.isConnected || started !== version) return;
      error.textContent = "";
      const ids = new Set(pending.map(request => request.id));
      for (const [id, existing] of cards) {
        if (!ids.has(id)) { existing.form.remove(); cards.delete(id); }
      }
      for (const request of pending) {
        const fingerprint = JSON.stringify(request);
        if (cards.get(request.id)?.fingerprint === fingerprint) continue;
        cards.get(request.id)?.form.remove();
        const form = card(request);
        cards.set(request.id, {form, fingerprint});
        target.append(form);
      }
      onWaiting(pending.length > 0);
    } catch (err) {
      if (target.isConnected) error.textContent = `Не удалось проверить вопросы: ${err.message}. Повторяем автоматически…`;
    } finally { loading = false; }
  }
  return {refresh};
}
