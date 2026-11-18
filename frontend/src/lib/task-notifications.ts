import { api } from "@/api/client"
import type { QueueTask } from "@/api/task-types"

export const TASK_NOTIFICATION_PAGE_SIZE = 50

export function isNotificationTask(task: QueueTask): boolean {
    return task.kind !== "fetch"
}
/** Read the full snapshot before changing any notification state. Never mutate the queue. */
export async function getAllTaskNotifications(): Promise<QueueTask[]> {
    const tasks: QueueTask[] = []
    let before: number | undefined
    for (;;) {
        const page = await api.getTasks(before === undefined ? undefined : { before })
        if (!Array.isArray(page)) throw new Error("invalid_task_page")
        tasks.push(...page.filter(isNotificationTask))
        if (page.length < TASK_NOTIFICATION_PAGE_SIZE) return tasks
        const next = page[page.length - 1].id
        if (before !== undefined && next >= before) throw new Error("invalid_task_cursor")
        before = next
    }
}
