from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from openpyxl import Workbook
from materflow.v42.export import sheet
from materflow.schema import file_hash,write_json

def main():
    out=ROOT/"docs/V4.2";out.mkdir(exist_ok=True)
    for filename,parts in (("observations_v4.2.template.xlsx",("Observations","Object Identities")),
                           ("events_v4.2.template.xlsx",("Events",)),
                           ("lineage_v4.2.template.xlsx",("Materials","Lineage"))):
        path=out/filename
        if path.exists():
            print("Preserved existing template",path);continue
        book=Workbook();book.remove(book.active)
        for part in parts:
            sheet(book,part,[])
        sheet(book,"Field Evidence",[])
        sheet(book,"说明",[{"key":"schema_version","value":"ChemFlow-v4.2",
            "meaning":"Public dates are source recording timestamps; blank template contains no observed facts"}])
        book.save(path);print(path)

if __name__=="__main__":
    main()
