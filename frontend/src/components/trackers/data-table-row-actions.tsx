import {
    MoreHorizontal,
    Play,
    Edit,
    Trash2
} from "lucide-react"
import type { Row } from "@tanstack/react-table"

import { Button } from "@/components/ui/button"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type { TrackerStatus } from "@/api/types"

type TrackerTableTranslator = (key: string) => string

interface DataTableRowActionsProps {
    row: Row<TrackerStatus>
    onEdit: (name: string) => void
    onDelete: (name: string) => void
    onCheck: (name: string) => void
    t: TrackerTableTranslator
}

export function DataTableRowActions({
    row,
    onEdit,
    onDelete,
    onCheck,
    t,
}: DataTableRowActionsProps) {
    const tracker = row.original

    return (
        <div className="text-right">
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <Button
                        variant="ghost"
                        className="flex size-8 p-0 data-[state=open]:bg-accent"
                    >
                        <MoreHorizontal className="size-4" />
                        <span className="sr-only">Open menu</span>
                    </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-[160px]">
                    <DropdownMenuItem onClick={() => onCheck(tracker.name)}>
                        <Play className="mr-2 h-4 w-4" /> {t('common.check')}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => onEdit(tracker.name)}>
                        <Edit className="mr-2 h-4 w-4" /> {t('common.edit')}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                        onClick={() => onDelete(tracker.name)}
                        className="text-destructive focus:text-destructive"
                    >
                        <Trash2 className="mr-2 h-4 w-4" /> {t('common.delete')}
                    </DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>
        </div>
    )
}
