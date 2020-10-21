function CompletionData(){
    $.ajax({
        dataType: 'json',
        type: 'GET',
        url: $('#data_url').val(),
        success: function(data) {
            if (data["data"][0][0] == false){
                setTimeout(CompletionData, 5000);
            }
            else{
                return CompletionTable(data);
            }
        },
        error: function() {
            return CompletionDataError();
        }
    })
}
function CompletionDataError(){
    var error = $('.eol-completion-error')[0]
    $('.loading')[0].style.display="none";
    error.innerHTML = "Ocurrio un error inesperado, actualice la página e intente nuevamente.</br>Si el error persiste contactese con el equipo ded EOL"
}
function CompletionTable(data){
    var main_header = $('.mainhead');
    var array_index_column_pto = []
    if(main_header.length > 0){
        var columns_pto = $('.pto');
        for (j = 0; j < columns_pto.length; j++) {
            array_index_column_pto.push(parseInt(columns_pto[j].id))
        }
        array_index_column_pto.push(parseInt(columns_pto[columns_pto.length-1].id)+1)
        array_index_column_pto.push(parseInt(columns_pto[columns_pto.length-1].id)+2)
        for (j = 0; j < main_header.length; j++) {
            var aux_i = main_header[j].dataset.col_from
            var aux_j = main_header[j].dataset.col_to
            $('#botones')[0].innerHTML = $('#botones')[0].innerHTML + ' <li><a class="toggle-vis novisto" value="' + aux_i + ',' + aux_j + '">' + main_header[j].textContent + '</a></li>'
        }
        var footer = $('#eol-completion-tfoot')[0];
        footer_aux = "<tr><th hidden></th><th>Total</th><th></th>";
        for (j = 0; j <data["completion"].length; j++) {  
            footer_aux = footer_aux + "<th>" + data["completion"][j] + "</th>";
        }
        footer.innerHTML = footer_aux +"</tr>";
    }
    else{
        var footer = $('#eol-completion-tfoot')[0];
        footer.innerHTML = "<tr><th hidden></th><th>Total</th><th></th><th></th><th></th><th></th></tr>";
    }
    var dataTable = []
        if (data["data"][0][0] != true){
            dataTable = data["data"]
        }
    var myTable = $('#mytable').DataTable({
            scrollX: true,
            rowReorder: true,
            data: dataTable,
            "processing": true,
            "sDom": "B<'row'>lfrtip",
            buttons: [ {
            extend: 'excelHtml5',
            title : 'Seguimiento'
            }],        
            fixedColumns: {
                leftColumns: 2
            },
            "pageLength": 100,
            "lengthMenu": [[50, 100, 200, -1], [50, 100, 200, "Todos"]],
            columnDefs: [
                { targets: [0], "visible": false, "searchable": false },
                { "type": 'natural', orderable: true, className: 'reorder', targets: array_index_column_pto },
                { orderable: true, className: 'reorder', targets: [1] },
                { orderable: false, targets: '_all' }
            ],
            language: {
                "decimal": "",
                "emptyTable": "No hay información",
                "info": "Mostrando _START_ a _END_ de _TOTAL_ Estudiantes",
                "infoEmpty": "Mostrando 0 de 0 a 0 Estudiantes",
                "infoFiltered": "(Filtrado de _MAX_ total Estudiantes)",
                "infoPostFix": "",
                "thousands": ",",
                "lengthMenu": "Mostrar _MENU_ Estudiantes",
                "loadingRecords": "Cargando...",
                "processing": "Procesando...",
                "search": "Buscar:",
                "zeroRecords": "Sin resultados encontrados",
                "paginate": {
                    "first": "Primero",
                    "last": "Ultimo",
                    "next": "Siguiente",
                    "previous": "Anterior"
                }
            }
        });
    $('.loading')[0].style.display="none";
    $('.teams-content')[0].style.visibility = "visible";
    $('#eol-completion-time')[0].innerHTML = "</br>Ultima Actualización: " + data["time"] + " - Próxima actualización disponible dentro de " + data["time_queue"] + " minutos"
    $('a.toggle-vis').on('click', function (e) {
        e.preventDefault();
        // Get the column API object
        var minmax = $(this).attr('value');
        var aux = minmax.split(',')
        var min = parseInt(aux[0])
        var max = parseInt(aux[1])
        
        for (i = min; i <= max; i++) {
            var column = myTable.column(i);
            column.visible(!column.visible());
        }
        if ($(this).hasClass('visto')){
            $(this).removeClass('visto')
            $(this).addClass('novisto')
        }
        else{
            $(this).removeClass('novisto')
            $(this).addClass('visto')
        }
    }); 
}

$(document).ready(function () {
    CompletionData();
});