import type { Table } from "@tanstack/react-table"
import { X, Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DataTableViewOptions } from "@/components/ui/data-table/data-table-view-options"

type TrackerTableTranslator = (key: string) => string

interface DataTableToolbarProps<TData> {
    table: Table<TData>
    t: TrackerTableTranslator
    onAdd: () => void
}

export function DataTableToolbar<TData>({
    table,
    t,
    onAdd,
}: DataTableToolbarProps<TData>) {
    const isFiltered = table.getState().columnFilters.length > 0

    return (
        <div className="flex items-center justify-between">
            <div className="flex flex-1 items-center space-x-2">
                <Input
                    placeholder={t('trackers.searchPlaceholder') || "Filter trackers..."}
                    value={(table.getColumn("name")?.getFilterValue() as string) ?? ""}
                    onChange={(event) =>
                        table.getColumn("name")?.setFilterValue(event.target.value)
                    }
                    className="h-8 w-[150px] lg:w-[250px]"
                />
                {isFiltered && (
                    <Button
                        variant="ghost"
                        onClick={() => table.resetColumnFilters()}
                        className="h-8 px-2 lg:px-3"
                    >
                        {t('common.reset') || "Reset"}
                        <X className="ml-2 h-4 w-4" />
                    </Button>
                )}
            </div>
            <div className="flex items-center gap-2">
                <DataTableViewOptions table={table} />
                <Button size="sm" className="h-8" onClick={onAdd}>
                    <Plus className="mr-2 h-4 w-4" />
                    {t('trackers.addNew')}
                </Button>
            </div>
        </div>
    )
}
