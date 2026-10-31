import { TriangleAlert } from "lucide-react"
import { Link, useLocation } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

export function NotFoundPage() {
  const location = useLocation()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TriangleAlert aria-hidden="true" className="size-4 text-amber-500" />
          页面不存在
        </CardTitle>
        <CardDescription>
          没有匹配到路由：
          <code className="ml-1 rounded bg-muted px-1 break-all">
            {location.pathname}
          </code>
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="outline" size="sm" asChild>
          <Link to="/">回到首页</Link>
        </Button>
      </CardContent>
    </Card>
  )
}
