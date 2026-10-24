// Skip byte-identical listing responses. Preserve scroll in the table container.
document.body.addEventListener('htmx:beforeSwap', function(event) {
  const d=event.detail;
  if (['deal-rows','lot-rows'].includes(d.target?.id) && d.xhr.responseText.trim() === d.target.innerHTML.trim()) d.shouldSwap=false;
});

// User filter edits return to page one; status updates preserve the current page.
for (const type of ['change','input']) document.body.addEventListener(type,function(event) {
  const form=event.target.closest('form.browse-filters');
  if(form && event.target.name !== 'page') { const page=form.querySelector('[name=page]'); if(page) page.value='1'; }
},true);
