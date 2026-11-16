import { useEffect, useRef, useState } from 'preact/hooks';

import type { Annotation } from '../../types/api';
import { quote } from '../helpers/annotation-metadata';
import { withServices } from '../service-context';
import type { AnnotationsService } from '../services/annotations';
import type { FrameSyncService } from '../services/frame-sync';
import { useSidebarStore } from '../store';
import { useUnsavedChanges } from './hooks/unsaved-changes';

type Props = {
  annotation: Annotation;
  annotationsService: AnnotationsService;
  frameSync: FrameSyncService;
};

function FloatingAnnotationEditor({
  annotation,
  annotationsService,
  frameSync,
}: Props) {
  const store = useSidebarStore();
  const draft = store.getDraft(annotation);
  const text = draft?.text ?? '';
  const editor = useRef<HTMLTextAreaElement>(null);
  const inFlight = useRef(false);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  useUnsavedChanges(!!text);

  useEffect(() => {
    editor.current?.focus();
  }, []);

  function close(openSidebar: boolean) {
    if (store.floatingAnnotationTag() !== annotation.$tag) {
      return;
    }
    store.setFloatingAnnotation(null);
    if (openSidebar) {
      store.clearSelection();
      store.selectTab('annotation');
    }
    frameSync.notifyHost(openSidebar ? 'openSidebar' : 'closeSidebar');
  }

  function dismiss(openSidebar: boolean) {
    if (inFlight.current) {
      return;
    }
    if (draft?.isEmpty()) {
      store.removeDraft(annotation);
      store.removeAnnotations([annotation]);
    } else if (draft) {
      store.createDraft(annotation, { ...draft, isPrivate: true });
    }
    close(openSidebar);
  }

  async function save() {
    if (!draft || !draft.text.trim() || inFlight.current) {
      return;
    }
    inFlight.current = true;
    setSaving(true);
    setFailed(false);
    try {
      store.createDraft(annotation, { ...draft, isPrivate: true });
      await annotationsService.save(annotation);
      close(false);
    } catch {
      setFailed(true);
      editor.current?.focus();
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }

  const buttonStyle =
    'grid size-7 shrink-0 place-items-center rounded-full text-[#3c4043] hover:bg-[#e5e6ee] focus-visible:outline-2 focus-visible:outline-[#1a73e8] disabled:opacity-40';

  return (
    <section
      aria-label="Quick annotation"
      className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)_auto] gap-2 bg-[#f0eff5] px-3 pt-0 font-sans text-[#202124]"
    >
      <div className="flex min-w-0 items-start gap-1">
        <blockquote className="min-w-0 flex-1 max-h-[42px] overflow-auto border-l-2 border-[#989eb3] pl-2 text-xs leading-4 text-[#444754] whitespace-pre-wrap break-words select-text">
          {quote(annotation)}
        </blockquote>
        <button
          type="button"
          className={buttonStyle}
          aria-label="Page annotations"
          title="Page annotations"
          disabled={saving}
          onClick={() => dismiss(true)}
        >
          <svg
            aria-hidden="true"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="M15 5v14M17.5 9h1M17.5 12h1" />
          </svg>
        </button>
        <button
          type="button"
          className={buttonStyle}
          aria-label="Close editor"
          title="Close editor"
          disabled={saving}
          onClick={() => dismiss(false)}
        >
          <svg
            aria-hidden="true"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
          >
            <path d="m6 6 12 12M18 6 6 18" />
          </svg>
        </button>
      </div>
      <textarea
        ref={editor}
        aria-label="Annotation text"
        aria-describedby="quick-annotation-status"
        className="min-h-0 w-full resize-none rounded-xl border border-[#c9cbd4] bg-white px-3 py-2 text-sm leading-5 outline-none focus:border-[#1a73e8] focus:ring-1 focus:ring-[#1a73e8]"
        placeholder="Add a thought…"
        value={text}
        readOnly={saving}
        onInput={event => {
          if (!inFlight.current && draft) {
            store.createDraft(annotation, {
              ...draft,
              isPrivate: true,
              text: event.currentTarget.value,
            });
          }
        }}
        onKeyDown={event => {
          if (event.isComposing || event.keyCode === 229) {
            return;
          }
          if (event.key === 'Escape') {
            event.preventDefault();
            dismiss(false);
          } else if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            void save();
          }
        }}
      />
      <div
        id="quick-annotation-status"
        role="status"
        className="min-h-4 pb-0.5 text-[11px] leading-4 text-[#525563]"
      >
        {saving
          ? 'Saving…'
          : failed
            ? 'Save failed. Press Enter to retry.'
            : 'Only me · Enter to save · Shift+Enter for a new line'}
      </div>
    </section>
  );
}

export default withServices(FloatingAnnotationEditor, [
  'annotationsService',
  'frameSync',
]);
