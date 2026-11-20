import { useEffect, useState } from "react"
import { checkDuplicate, type Duplicate, type Library } from "./api"
import { previewKey } from "./previewState"

type Result = { input: string; localFile: boolean; library: Library | undefined; duplicate: Duplicate }

/** Background lookup only affects the current input. Submission always checks again. */
export function useSourceDuplicate(input: string, localFile: boolean, library: Library | undefined) {
  const [result, setResult] = useState<Result>()
  useEffect(() => {
    if (!input || (!localFile && !previewKey(input))) return
    let current = true
    const timer = window.setTimeout(() => {
      void checkDuplicate(input, localFile).then(duplicate => {
        if (current) setResult({ input, localFile, library, duplicate })
      }, () => { /* The explicit start action reports lookup failures. */ })
    }, 200)
    return () => { current = false; window.clearTimeout(timer) }
  }, [input, localFile, library])
  return result?.input === input && result.localFile === localFile && result.library === library ? result.duplicate : undefined
}
