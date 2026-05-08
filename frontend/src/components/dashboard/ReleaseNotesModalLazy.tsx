import { Suspense, lazy } from "react"

import type { ReleaseNotesSubject } from "@/api/types"

const ReleaseNotesModalImpl = lazy(() =>
    import("./ReleaseNotesModal").then((module) => ({ default: module.ReleaseNotesModal })),
)

interface ReleaseNotesModalLazyProps {
    release: ReleaseNotesSubject | null
    open: boolean
    onOpenChange: (open: boolean) => void
}

/**
 * Lazy-loading wrapper for the ReleaseNotesModal.
 *
 * The modal pulls in react-markdown + remark/rehype plugins (~118 KB gzipped)
 * that would otherwise be bundled into every route that might surface release
 * notes (Dashboard, History, Trackers). By deferring the real component until
 * the modal is first opened we keep those routes lean.
 */
export function ReleaseNotesModal(props: ReleaseNotesModalLazyProps) {
    // Before first open we render nothing — the modal is closed anyway.
    if (!props.open && !props.release) {
        return null
    }

    return (
        <Suspense fallback={null}>
            <ReleaseNotesModalImpl {...props} />
        </Suspense>
    )
}
