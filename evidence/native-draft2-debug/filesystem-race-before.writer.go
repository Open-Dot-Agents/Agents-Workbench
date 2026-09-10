package main
import("os";"io/fs";"path/filepath";"encoding/json";"fmt")
// This is the pre-change nativeAtomicWrite function, isolated from the recorded
// native.go working-tree revision. Only the main function is a new reproducer.
func nativeAtomicWrite(path string,data []byte,mode fs.FileMode)error{
 f,err:=os.CreateTemp(filepath.Dir(path),".agents-native-*");if err!=nil{return err}
 name:=f.Name();defer os.Remove(name)
 if err=f.Chmod(mode);err==nil{_,err=f.Write(data)}
 if err==nil{err=f.Sync()};closeErr:=f.Close();if err==nil{err=closeErr};if err!=nil{return err}
 return os.Rename(name,path)
}
func main(){
 base:=os.Args[1];parent:=filepath.Join(base,"native");outside:=filepath.Join(base,"outside")
 must:=func(err error){if err!=nil{panic(err)}}
 must(os.Mkdir(parent,0700));must(os.Mkdir(outside,0700))
 sentinel:=filepath.Join(outside,"config.toml");must(os.WriteFile(sentinel,[]byte("outside"),0600))
 // Both parent directories exist and are regular directories before replacement.
 info,err:=os.Lstat(parent);must(err);if !info.IsDir(){panic("invalid fixture")}
 must(os.Rename(parent,parent+"-saved"));must(os.Symlink(outside,parent))
 err=nativeAtomicWrite(filepath.Join(parent,"config.toml"),[]byte("projected"),0600)
 after,readErr:=os.ReadFile(sentinel);must(readErr)
 json.NewEncoder(os.Stdout).Encode(map[string]any{"write_error":fmt.Sprint(err),"outside_before":"outside","outside_after":string(after),"redirected":string(after)=="projected"})
}
